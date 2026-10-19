"""Spawn the real MCP stdio server against a loopback dashboard and audit what it touches."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

try:
    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import CallToolResult, TextContent
except ImportError:  # MCP is an optional extra; CI installs it.
    raise unittest.SkipTest("MCP SDK not installed") from None

from _fixtures import CSRF
from _support import LocalServer, Reply

REPO = Path(__file__).resolve().parent.parent
PASSWORD = "hunter2-PASSWORD-9f1c"
COOKIE = "COOKIE-SECRET-41d2"
SECRETS = (PASSWORD, COOKIE, CSRF)
NETWORK = ("socket.connect", "socket.bind", "socket.getaddrinfo")
SPAWNS = ("subprocess.Popen", "os.posix_spawn", "os.exec", "os.fork", "webbrowser.open")

AUDIT_HOOK = f"""
import json, os, sys
_log = os.environ.get("OUTCRAFT_TEST_AUDIT")
_watched = {set(NETWORK + SPAWNS)!r}

def _hook(event, args):
    if _log and event in _watched:
        detail = args[1] if event in ("socket.connect", "socket.bind") else args[0]
        with open(_log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps([event, repr(detail)]) + "\\n")

sys.addaudithook(_hook)
"""


class ScriptedServer(LocalServer):
    """LocalServer whose routes may also be computed per request from recorded state."""

    def __init__(self) -> None:
        super().__init__()
        self._handlers: dict[tuple[str, str], Callable[[], Reply]] = {}

    def route_fn(self, method: str, path: str, handler: Callable[[], Reply]) -> None:
        self._handlers[(method.upper(), path)] = handler

    def posts(self) -> int:
        return sum(1 for request in self.requests if request.method == "POST")

    def _next_reply(self, method: str, path: str) -> Reply:
        handler = self._handlers.get((method, urlsplit(path).path))
        return handler() if handler else super()._next_reply(method, path)


class Concurrency:
    """A slow reply that records how many requests the dashboard served at once."""

    def __init__(self, reply: Reply, seconds: float) -> None:
        self.reply, self.seconds = reply, seconds
        self.active = self.peak = 0
        self._lock = threading.Lock()

    def __call__(self) -> Reply:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.seconds)
        with self._lock:
            self.active -= 1
        return self.reply


@dataclass
class Session:
    client: Client
    audit: Path
    stderr: Path
    outputs: list[str] = field(default_factory=list)

    async def call(self, name: str, arguments: dict[str, object] | None = None) -> CallToolResult:
        result = await self.client.call_tool(name, arguments or {})
        self.outputs.extend(c.text for c in result.content if isinstance(c, TextContent))
        return result

    def events(self) -> list[tuple[str, str]]:
        if not self.audit.exists():
            return []
        lines = self.audit.read_text(encoding="utf-8").splitlines()
        return [(event, detail) for event, detail in map(json.loads, lines)]

    def exposed(self) -> str:
        return "\n".join(self.outputs) + "\n" + self.stderr.read_text(encoding="utf-8")


def write_env(directory: Path, *, valid: bool) -> Path:
    path = directory / ("valid.env" if valid else "invalid.env")
    lines = ["OUTCRAFT_EMAIL=agent@example.test", f"OUTCRAFT_PASSWORD={PASSWORD}"]
    if not valid:
        lines.insert(0, "this line is not KEY=value")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600 if valid else 0o644)
    return path


@asynccontextmanager
async def mcp_session(base_url: str, directory: Path, env_file: Path) -> AsyncIterator[Session]:
    hooks = directory / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "sitecustomize.py").write_text(AUDIT_HOOK)
    audit, stderr = directory / "audit.jsonl", directory / "stderr.log"
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "outcraft_cli.mcp_server",
            "--base-url",
            base_url,
            "--session-file",
            str(directory / "session.txt"),
            "--env-file",
            str(env_file),
        ],
        env={
            "HOME": str(directory),
            "PYTHONPATH": os.pathsep.join([str(hooks), str(REPO)]),
            "OUTCRAFT_TEST_AUDIT": str(audit),
        },
        cwd=str(REPO),
    )
    with stderr.open("w", encoding="utf-8") as errlog:
        async with Client(stdio_client(params, errlog=errlog)) as client:  # type: ignore[arg-type]
            yield Session(client, audit, stderr)


def texts(result: CallToolResult) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


def payload(result: CallToolResult) -> object:
    blocks = [c.text for c in result.content if isinstance(c, TextContent)]
    if result.is_error or len(blocks) != 1:
        raise AssertionError(f"Expected 1 JSON text block, got: {result!r}")
    return json.loads(blocks[0])
