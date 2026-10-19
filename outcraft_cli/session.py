from __future__ import annotations

import ipaddress
import json
import os
import tempfile
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from urllib.parse import urlsplit

from .errors import OutcraftError

IDENTITY_HEADERS = frozenset(
    {"user-agent", "accept-language", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"}
)


def identity_headers(headers: dict[str, str]) -> dict[str, str]:
    selected = {k.lower(): v for k, v in headers.items() if k.lower() in IDENTITY_HEADERS}
    if any("\r" in v or "\n" in v for v in selected.values()):
        raise OutcraftError("Invalid browser identity headers.")
    return selected


def session_cookie(data: dict[str, object], origin: str) -> Cookie | None:
    name, value, domain, path = (data.get(k) for k in ("name", "value", "domain", "path"))
    if (
        not isinstance(name, str)
        or not isinstance(value, str)
        or not isinstance(domain, str)
        or not isinstance(path, str)
    ):
        raise OutcraftError("Invalid session cookie.")
    host = urlsplit(origin).hostname or ""
    cookie_host = domain.lstrip(".").lower()
    try:
        ipaddress.ip_address(host)
        allowed = host == cookie_host
    except ValueError:
        allowed = host == cookie_host or host.endswith("." + cookie_host)
    if not allowed or not cookie_host:
        return None
    expires = data.get("expires")
    if expires is not None and not isinstance(expires, (int, float)):
        raise OutcraftError("Invalid session cookie expiry.")
    expiry = int(expires) if expires is not None and expires > 0 else None
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=domain.startswith("."),
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=True,
        secure=bool(data.get("secure", False)),
        expires=expiry,
        discard=expiry is None,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": ""} if data.get("httpOnly") else {},
        rfc2109=False,
    )


def load_session(path: Path | None, origin: str, jar: CookieJar) -> dict[str, str]:
    if path is None:
        return {}
    if path.is_symlink():
        raise OutcraftError("Session file must not be a symlink.")
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise OutcraftError("Session file must be private and must not be a symlink.")
    try:
        text = path.read_text()
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("origin") != origin:
            raise OutcraftError(
                "Session belongs to a different origin. Use a separate session file."
            )
        headers, cookies = data.get("headers"), data.get("cookies")
        if (
            not isinstance(headers, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
            or not isinstance(cookies, list)
        ):
            raise ValueError
        for item in cookies:
            if not isinstance(item, dict):
                raise ValueError
            cookie = session_cookie(item, origin)
            if cookie:
                jar.set_cookie(cookie)
        return identity_headers(headers)
    except (OSError, ValueError, TypeError):
        raise OutcraftError(
            "Cannot read session. Remove it with auth logout, then log in again."
        ) from None


def save_session(path: Path | None, origin: str, jar: CookieJar, headers: dict[str, str]) -> None:
    if path is None:
        return
    if path.is_symlink():
        raise OutcraftError("Session file must not be a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {
        "origin": origin,
        "headers": identity_headers(headers),
        "cookies": [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "secure": c.secure,
                "expires": c.expires,
                "httpOnly": c.has_nonstandard_attr("HttpOnly"),
            }
            for c in jar
            if not c.is_expired()
        ],
    }
    fd, temporary = tempfile.mkstemp(prefix=".outcraft-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(data, output)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
