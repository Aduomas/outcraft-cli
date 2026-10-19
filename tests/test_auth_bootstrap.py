"""Fresh sessions recover a browser challenge before any campaign operation."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from _browser_fixtures import (
    EMAIL,
    PASSWORD,
    SESSION,
    BrowserLoginServer,
    assert_browser_closed,
    browser_processes,
    require_chromium,
    without_browser,
)
from _campaign_fixtures import campaign_page
from _support import Reply

from outcraft_cli.cli import main
from outcraft_cli.client import Client
from outcraft_cli.transport import Transport

EXPECTED = [{"id": 7, "name": "Synthetic campaign", "status": "Paused"}]


class ChallengeServer(BrowserLoginServer):
    def _next_reply(self, method: str, path: str) -> Reply:
        if method == "GET" and urlsplit(path).path == "/campaigns":
            request = self.requests_to(method, path)[-1]
            if request.cookies().get("outcraft_session") != SESSION:
                return Reply.html("Browser session required", status=403)
            if request.headers.get("user-agent") == self.login_user_agent:
                return Reply.html(campaign_page())
            return Reply.html("Browser identity required", status=403)
        return super()._next_reply(method, path)


class AuthBootstrapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        require_chromium()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.session_file = self.directory / "session.json"
        self.env_file = self.directory / "credentials.env"
        self.env_file.write_text(f"OUTCRAFT_EMAIL={EMAIL}\nOUTCRAFT_PASSWORD={PASSWORD}\n")
        self.env_file.chmod(0o600)
        self.server = ChallengeServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        before = browser_processes()
        self.addCleanup(assert_browser_closed, self, before)

    def assert_bootstrapped(self) -> None:
        first = self.server.requests[0]
        self.assertEqual((first.method, first.path), ("GET", "/campaigns"))
        self.assertNotIn("outcraft_session", first.cookies())
        self.assertNotEqual(first.headers.get("sec-fetch-dest"), "document")
        self.assertEqual(len(self.server.submissions), 1, "Login must not be retried")
        self.assertEqual(
            [
                (request.method, request.path)
                for request in self.server.requests
                if request.method == "POST"
            ],
            [("POST", "/auth/session")],
        )
        authenticated_reads = self.server.http_reads()[1:]
        self.assertGreaterEqual(len(authenticated_reads), 2)
        for request in authenticated_reads:
            self.assertEqual(request.cookies().get("outcraft_session"), SESSION)
            self.assertEqual(request.headers.get("user-agent"), self.server.login_user_agent)
        self.assertTrue(self.session_file.is_file())

    def test_client_recovers_initial_challenge_then_uses_httpx(self) -> None:
        with Transport(self.server.base_url, self.session_file, timeout=5) as transport:
            client = Client(transport)
            client.ensure_authenticated(EMAIL, PASSWORD)
            self.assertEqual(client.campaigns(), EXPECTED)
        self.assert_bootstrapped()
        self.assertEqual(
            without_browser(
                self.server.base_url,
                "client.ensure_authenticated(); result = client.campaigns()",
                self.session_file,
            ),
            EXPECTED,
        )
        self.assertEqual(len(self.server.submissions), 1)

    def test_cli_first_campaign_list_bootstraps_with_private_env_file(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"OUTCRAFT_EMAIL": "", "OUTCRAFT_PASSWORD": ""}):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(
                    [
                        "--env-file",
                        str(self.env_file),
                        "--base-url",
                        self.server.base_url,
                        "--session-file",
                        str(self.session_file),
                        "campaigns",
                        "list",
                    ]
                )
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue()), EXPECTED)
        self.assertNotIn(PASSWORD, stdout.getvalue() + stderr.getvalue())
        self.assert_bootstrapped()

    def test_mcp_first_campaign_list_bootstraps_over_real_stdio(self) -> None:
        try:
            import anyio
            from mcp import Client as McpClient
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.types import TextContent
        except ImportError:
            self.skipTest("Optional MCP SDK unavailable")

        async def call() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "outcraft_cli.mcp_server",
                    "--env-file",
                    str(self.env_file),
                    "--base-url",
                    self.server.base_url,
                    "--session-file",
                    str(self.session_file),
                ],
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key in ("HOME", "PATH", "PLAYWRIGHT_BROWSERS_PATH")
                },
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            with (self.directory / "mcp-stderr.log").open("w") as stderr:
                with anyio.fail_after(45):
                    async with McpClient(stdio_client(params, errlog=stderr)) as client:  # type: ignore[arg-type]
                        await client.list_tools()
                        self.assertEqual(
                            self.server.requests, [], "Discovery must not authenticate"
                        )
                        result = await client.call_tool("list_campaigns", {})
                        blocks = [
                            item.text for item in result.content if isinstance(item, TextContent)
                        ]
                        self.assertFalse(result.is_error, "\n".join(blocks))
                        self.assertEqual(len(blocks), 1)
                        self.assertEqual(json.loads(blocks[0]), EXPECTED)
                        self.assertNotIn(PASSWORD, blocks[0])
            self.assertNotIn(PASSWORD, (self.directory / "mcp-stderr.log").read_text())

        anyio.run(call)
        self.assert_bootstrapped()

    def test_server_failure_does_not_trigger_browser_login(self) -> None:
        with BrowserLoginServer() as server:
            server.route("GET", "/campaigns", Reply.html("Unavailable", status=500))
            result = without_browser(
                server.base_url,
                f"""
try:
    client.ensure_authenticated({EMAIL!r}, {PASSWORD!r})
except OutcraftError as error:
    result = str(error)
else:
    raise AssertionError("HTTP 500 was ignored")
""",
            )
            self.assertIn("HTTP 500", result)
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(server.submissions, [])


if __name__ == "__main__":
    unittest.main()
