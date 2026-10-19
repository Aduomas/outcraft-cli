"""Login origin guards reject credential leaks without treating assets as login failures."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _browser_fixtures import (
    EMAIL,
    PASSWORD,
    BrowserLoginServer,
    assert_browser_closed,
    browser_processes,
    require_chromium,
)
from _support import LocalServer, Reply

from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport


class BackgroundResourceServer(BrowserLoginServer):
    def __init__(self, *, beacon_url: str | None = None, image: bool = False) -> None:
        super().__init__()
        self.beacon_url = beacon_url
        self.image = image

    def login_form(self) -> str:
        document = super().login_form()
        background = ""
        if self.beacon_url:
            background += (
                "<script>navigator.sendBeacon("
                + json.dumps(self.beacon_url)
                + ", 'telemetry-without-credentials');</script>"
            )
        if self.image:
            document = document.replace('id="email" name=', 'id="email" disabled name=')
            background += """<img src="/public-image.svg" alt="Synthetic logo"
onload="document.querySelector('#email').disabled = false"
onerror="document.querySelector('#email').disabled = false">"""
        return document.replace("</body>", background + "</body>")


class LoginGuardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        require_chromium()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session_file = Path(temporary.name) / "session.json"
        before = browser_processes()
        self.addCleanup(assert_browser_closed, self, before)

    def login(self, server: BrowserLoginServer) -> dict[str, object]:
        with Transport(server.base_url, self.session_file, timeout=3) as transport:
            return Client(transport).login(EMAIL, PASSWORD)

    def assert_no_foreign_credentials(self, server: LocalServer) -> None:
        for request in server.requests:
            self.assertNotIn(PASSWORD.encode(), request.body)
            self.assertNotIn(PASSWORD, request.path)
            self.assertNotIn(PASSWORD, json.dumps(request.headers))

    def test_blocked_analytics_beacon_does_not_fail_successful_login(self) -> None:
        with LocalServer() as foreign:
            foreign.route("POST", "/analytics", Reply(status=204))
            with BackgroundResourceServer(beacon_url=foreign.base_url + "/analytics") as server:
                self.assertEqual(
                    self.login(server), {"authenticated": True, "origin": server.base_url}
                )
                self.assertEqual(len(server.submissions), 1)
                self.assertTrue(server.http_reads())
            self.assertEqual(foreign.requests_to("POST", "/analytics"), [])
            self.assert_no_foreign_credentials(foreign)

    def test_blocked_telemetry_does_not_misdiagnose_login_validation_failure(self) -> None:
        with LocalServer() as foreign:
            foreign.route("POST", "/analytics", Reply(status=204))
            with BackgroundResourceServer(beacon_url=foreign.base_url + "/analytics") as server:
                server.route(
                    "POST",
                    "/auth/session",
                    Reply.json({"message": "Additional validation required"}),
                )
                with self.assertRaises(OutcraftError) as caught:
                    self.login(server)
                self.assertNotIn("Cross-origin", str(caught.exception))
                self.assertNotIn(PASSWORD, str(caught.exception))
                self.assertEqual(len(server.requests_to("POST", "/auth/session")), 1)
                self.assertFalse(self.session_file.exists())
            self.assertEqual(foreign.requests_to("POST", "/analytics"), [])
            self.assert_no_foreign_credentials(foreign)

    def test_public_image_redirect_does_not_fail_successful_login(self) -> None:
        with LocalServer() as foreign:
            foreign.route(
                "GET",
                "/logo.svg",
                Reply(
                    body='<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
                    headers=[("Content-Type", "image/svg+xml")],
                ),
            )
            with BackgroundResourceServer(image=True) as server:
                server.route(
                    "GET", "/public-image.svg", Reply.redirect(foreign.base_url + "/logo.svg")
                )
                self.assertEqual(
                    self.login(server), {"authenticated": True, "origin": server.base_url}
                )
                self.assertEqual(len(server.submissions), 1)
                self.assertTrue(server.http_reads())
            self.assertEqual(len(foreign.requests_to("GET", "/logo.svg")), 1)
            self.assertEqual(
                [request for request in foreign.requests if request.method == "POST"], []
            )
            self.assert_no_foreign_credentials(foreign)

    def test_foreign_credential_post_reports_origin_block_not_mfa(self) -> None:
        with LocalServer() as foreign:
            with BrowserLoginServer(form_action=foreign.base_url + "/steal") as server:
                with self.assertRaisesRegex(OutcraftError, "Cross-origin") as caught:
                    self.login(server)
                self.assertNotIn(PASSWORD, str(caught.exception))
                self.assertEqual(server.submissions, [])
            self.assertEqual(foreign.requests, [])


if __name__ == "__main__":
    unittest.main()
