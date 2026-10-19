from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from http.client import HTTPMessage
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path
from typing import IO
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from .errors import OutcraftError


@dataclass(frozen=True)
class Response:
    url: str
    text: str
    status: int


class _OriginRedirects(HTTPRedirectHandler):
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> Request | None:
        try:
            self.transport.url(newurl)
            if req.method not in ("GET", "HEAD"):
                raise OutcraftError(
                    "Unexpected redirect after a write. Verify state before retrying."
                )
        except OutcraftError:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def http_error_302(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage
    ) -> object:
        # urllib normalizes // redirects before redirect_request; reject their raw form first.
        location = headers.get("Location", "")
        if location.startswith("//"):
            fp.close()
            raise OutcraftError("Protocol-relative redirect refused.")
        result: object = super().http_error_302(req, fp, code, msg, headers)
        return result

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class Transport:
    def __init__(
        self,
        base_url: str = "https://app.outcraft.ai",
        session_file: Path | None = None,
        timeout: float = 30,
    ) -> None:
        parsed = urlsplit(base_url)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if (
            parsed.scheme != "https" and not (local and parsed.scheme == "http")
        ) or not parsed.netloc:
            raise OutcraftError("Use HTTPS, or HTTP on loopback for local tests only.")
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise OutcraftError("The base URL must be an origin without credentials or a path.")
        self.base_url = base_url.rstrip("/")
        self.session_file = session_file
        self.timeout = timeout
        self.cookies = MozillaCookieJar()
        if session_file and session_file.exists():
            if session_file.is_symlink():
                raise OutcraftError("Session file must not be a symlink.")
            try:
                self.cookies.load(str(session_file), ignore_discard=True)
            except (OSError, LoadError) as exc:
                raise OutcraftError(
                    "Cannot read the session file. Log in with a fresh session file."
                ) from exc
        self.opener = build_opener(HTTPCookieProcessor(self.cookies), _OriginRedirects(self))

    def url(self, path: str) -> str:
        if path.startswith("//") or "\\" in path or any(ord(c) < 32 for c in path):
            raise OutcraftError("Invalid or protocol-relative request URL.")
        url = urljoin(self.base_url + "/", path)
        parsed, base = urlsplit(url), urlsplit(self.base_url)
        if (
            (parsed.scheme, parsed.hostname, parsed.port) != (base.scheme, base.hostname, base.port)
            or parsed.username
            or parsed.password
        ):
            raise OutcraftError(
                "Cross-origin requests are refused; credentials stay on the configured origin."
            )
        return url

    def request(self, method: str, path: str, payload: object = None) -> Response:
        method = method.upper()
        url = self.url(path)
        headers = {"User-Agent": "outcraft-cli/0.1", "Accept": "text/html, application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers.update(
                {
                    "Content-Type": "application/json",
                    "X-Livewire": "true",
                    "Origin": self.base_url,
                    "Referer": self.base_url + "/",
                }
            )
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as reply:
                response = Response(reply.url, reply.read().decode("utf-8"), reply.status)
        except HTTPError as exc:
            exc.close()
            advice = {
                401: "Log in again.",
                403: "Access denied or browser challenge; use the dashboard.",
                419: "Session or CSRF expired; log in again.",
                422: "Validation failed; check input fields.",
                429: "Rate limited; wait before retrying.",
            }.get(exc.code, "Verify state before retrying a write.")
            raise OutcraftError(f"Outcraft returned HTTP {exc.code}. {advice}") from None
        except (URLError, TimeoutError, OSError, UnicodeError):
            raise OutcraftError(
                "Request failed or timed out. Verify state before retrying a write."
            ) from None
        finally:
            self._save()
        return response

    def _save(self) -> None:
        if self.session_file is None:
            return
        parent = self.session_file.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".outcraft-", dir=parent)
        try:
            os.close(fd)
            self.cookies.save(temporary, ignore_discard=True)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.session_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
