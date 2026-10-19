from __future__ import annotations

import json
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .errors import AuthenticationRequired, OutcraftError
from .session import identity_headers, load_session, save_session, session_cookie


@dataclass(frozen=True)
class Response:
    url: str
    text: str
    status: int


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
        self.cookies = CookieJar()
        self.headers = load_session(session_file, self.base_url, self.cookies)
        self._http = httpx.Client(
            cookies=self.cookies, timeout=timeout, follow_redirects=False, trust_env=False
        )
        self._referer = self.base_url + "/"

    def __enter__(self) -> Transport:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

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

    def adopt_browser_session(
        self, cookies: list[dict[str, object]], headers: dict[str, str]
    ) -> None:
        replacement = CookieJar()
        for item in cookies:
            cookie = session_cookie(item, self.base_url)
            if cookie is not None:
                replacement.set_cookie(cookie)
        selected = identity_headers(headers)
        self.cookies.clear()
        for cookie in replacement:
            self.cookies.set_cookie(cookie)
        self.headers = selected
        self._save()

    def request(self, method: str, path: str, payload: object = None) -> Response:
        method = method.upper()
        url = self.url(path)
        headers = {
            "user-agent": "outcraft-cli/0.1",
            "accept": "text/html, application/json",
            **self.headers,
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers.update(
                {
                    "content-type": "application/json",
                    "x-livewire": "true",
                    "origin": self.base_url,
                    "referer": self._referer,
                }
            )
        try:
            for _ in range(11):
                with self._http.stream(method, url, headers=headers, content=body) as reply:
                    reply.read()
                    if reply.status_code in (301, 302, 303, 307, 308):
                        location = reply.headers.get("location", "")
                        if location.startswith("//"):
                            raise OutcraftError("Protocol-relative redirect refused.")
                        destination = self.url(urljoin(str(reply.url), location))
                        if method not in ("GET", "HEAD"):
                            raise OutcraftError(
                                "Unexpected redirect after a write. Verify state before retrying."
                            )
                        url = destination
                        continue
                    if reply.status_code >= 400:
                        advice = {
                            401: "Log in again.",
                            403: "Access denied or browser challenge. Run auth login again.",
                            419: "Session or CSRF expired; log in again.",
                            422: "Validation failed; check input fields.",
                            429: "Rate limited; wait before retrying.",
                        }.get(reply.status_code, "Verify state before retrying a write.")
                        error = (
                            AuthenticationRequired
                            if reply.status_code in (401, 403, 419)
                            else OutcraftError
                        )
                        raise error(f"Outcraft returned HTTP {reply.status_code}. {advice}")
                    response = Response(
                        str(reply.url), reply.content.decode("utf-8"), reply.status_code
                    )
                    if method == "GET":
                        self._referer = response.url
                    return response
            raise OutcraftError("Too many redirects. Log in again.")
        except (httpx.RequestError, OSError, UnicodeError):
            raise OutcraftError(
                "Request failed or timed out. Verify state before retrying a write."
            ) from None
        finally:
            self._save()

    def _save(self) -> None:
        save_session(self.session_file, self.base_url, self.cookies, self.headers)
