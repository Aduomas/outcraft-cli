"""Local HTTP server for contract tests. Binds 127.0.0.1 only; never touches the network."""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


@dataclass
class Reply:
    status: int = 200
    body: str | bytes = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    delay: float = 0.0

    @classmethod
    def json(cls, payload: object, status: int = 200, cookies: list[str] | None = None) -> Reply:
        headers = [("Content-Type", "application/json")]
        headers += [("Set-Cookie", c) for c in cookies or []]
        return cls(status=status, body=json.dumps(payload), headers=headers)

    @classmethod
    def html(cls, body: str, status: int = 200, cookies: list[str] | None = None) -> Reply:
        headers = [("Content-Type", "text/html; charset=utf-8")]
        headers += [("Set-Cookie", c) for c in cookies or []]
        return cls(status=status, body=body, headers=headers)

    @classmethod
    def redirect(cls, location: str, status: int = 302) -> Reply:
        return cls(status=status, headers=[("Location", location)])


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8"))

    def cookies(self) -> dict[str, str]:
        jar = SimpleCookie()
        jar.load(self.headers.get("cookie", ""))
        return {name: morsel.value for name, morsel in jar.items()}


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        pass


class LocalServer:
    """Scripted server. Each route serves its replies in order; the last one repeats."""

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self._routes: dict[tuple[str, str], list[Reply]] = {}
        self._lock = threading.Lock()
        self._httpd = _Server(("127.0.0.1", 0), self._handler_class())
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def netloc(self) -> str:
        return urlsplit(self.base_url).netloc

    def route(self, method: str, path: str, *replies: Reply) -> None:
        with self._lock:
            self._routes[(method.upper(), path)] = list(replies)

    def requests_to(self, method: str, path: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method and urlsplit(r.path).path == path]

    def __enter__(self) -> LocalServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def _next_reply(self, method: str, path: str) -> Reply:
        with self._lock:
            replies = self._routes.get((method, urlsplit(path).path))
            if not replies:
                return Reply(status=404, body="no route")
            return replies.pop(0) if len(replies) > 1 else replies[0]

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                server.requests.append(Recorded(self.command, self.path, headers, body))
                reply = server._next_reply(self.command, self.path)
                if reply.delay:
                    time.sleep(reply.delay)
                payload = reply.body.encode("utf-8") if isinstance(reply.body, str) else reply.body
                self.send_response(reply.status)
                for name, value in reply.headers:
                    self.send_header(name, value)
                self.send_header("Connection", "close")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve

        return Handler


def unused_local_url() -> str:
    """A loopback URL with nothing listening, for connection-refused tests."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"
