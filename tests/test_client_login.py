"""Public login contract exercised through Chromium and JavaScript, never raw HTTP login."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
from _fixtures import UPDATE_URI, component_html, page_html, snapshot_json
from _support import LocalServer, Reply

from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport


class MissingCredentialsTest(unittest.TestCase):
    def test_missing_credentials_start_no_browser_and_send_no_http(self) -> None:
        with LocalServer() as server:
            result = without_browser(
                server.base_url,
                """
result = []
for email, password in [("", ""), ("", "synthetic-password"), ("synthetic@example.invalid", "")]:
    try:
        client.login(email, password)
    except OutcraftError as exc:
        result.append(str(exc))
    else:
        raise AssertionError("Missing credentials were accepted")
""",
            )
            self.assertEqual(len(result), 3)
            self.assertEqual(server.requests, [])


class BrowserClientLoginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        require_chromium()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session_file = Path(temporary.name) / "session.json"
        self.before = browser_processes()
        self.addCleanup(assert_browser_closed, self, self.before)

    def server(self, **kwargs: object) -> BrowserLoginServer:
        server = BrowserLoginServer(**kwargs).__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        return server

    def client(self, server: LocalServer) -> Client:
        return Client(
            Transport(base_url=server.base_url, session_file=self.session_file, timeout=3)
        )

    def assert_safe_error(self, error: OutcraftError) -> None:
        rendered = f"{error} {error!r} {error.args!r}"
        self.assertNotIn(EMAIL, rendered)
        self.assertNotIn(PASSWORD, rendered)

    def test_two_step_login_persists_identity_then_new_process_reads_without_browser(self) -> None:
        server = self.server(two_step=True)
        client = self.client(server)

        result = client.login(EMAIL, PASSWORD)

        self.assertEqual(result, {"authenticated": True, "origin": server.base_url})
        self.assertEqual(len(server.submissions), 1)
        self.assertIs(server.submissions[0]["webdriver"], True)
        self.assertIn("HeadlessChrome", server.login_user_agent)
        self.assertEqual(server.requests_to("POST", UPDATE_URI), [])
        self.assertTrue(server.http_reads(), "Login did not verify campaigns outside the browser")
        assert_browser_closed(self, self.before)
        count = len(server.http_reads())
        login_gets = len(server.requests_to("GET", "/auth/login"))
        restored = without_browser(
            server.base_url,
            "client.ensure_authenticated(); result = client.status()",
            self.session_file,
        )
        self.assertEqual(restored, result)
        self.assertGreater(len(server.http_reads()), count)
        self.assertEqual(len(server.requests_to("GET", "/auth/login")), login_gets)
        for request in server.http_reads():
            self.assertEqual(request.cookies().get("outcraft_session"), SESSION)
            self.assertEqual(request.headers.get("user-agent"), server.login_user_agent)
        persisted = self.session_file.read_text()
        self.assertIn(server.login_user_agent, persisted)
        self.assertNotIn(PASSWORD, persisted)
        self.assertNotIn(EMAIL, persisted)

    def test_one_step_login_and_second_explicit_login_use_fresh_browser_contexts(self) -> None:
        server = self.server()
        client = self.client(server)
        for _ in range(2):
            self.assertIs(client.login(EMAIL, PASSWORD)["authenticated"], True)
            assert_browser_closed(self, self.before)
        login_requests = server.requests_to("GET", "/auth/login")
        self.assertEqual(len(login_requests), 2)
        self.assertTrue(all(not request.cookies() for request in login_requests))
        self.assertEqual(len(server.submissions), 2)
        self.assertTrue(all(submission["webdriver"] is True for submission in server.submissions))

    def test_invalid_credentials_are_safe_and_browser_is_closed(self) -> None:
        server = self.server(valid_credentials=False)
        with self.assertRaises(OutcraftError) as caught:
            self.client(server).login(EMAIL, PASSWORD)
        self.assert_safe_error(caught.exception)
        self.assertEqual(len(server.submissions), 1, "Credential submission must not be retried")
        self.assertEqual(server.http_reads(), [])
        self.assertFalse(self.session_file.exists(), "Failed credentials must not create a session")

    def test_browser_redirect_is_not_success_when_httpx_campaign_index_is_unauthenticated(
        self,
    ) -> None:
        server = self.server(http_authenticated=False)
        with self.assertRaises(OutcraftError) as caught:
            self.client(server).login(EMAIL, PASSWORD)
        self.assert_safe_error(caught.exception)
        self.assertEqual(len(server.submissions), 1)
        self.assertTrue(server.http_reads(), "The HTTPX authentication check never ran")
        self.assertTrue(
            any(
                request.headers.get("sec-fetch-dest") == "document"
                for request in server.requests_to("GET", "/campaigns")
            ),
            "The synthetic browser never completed its successful redirect",
        )

    def test_httpx_verification_requires_campaign_table_not_just_any_component(self) -> None:
        server = self.server()
        server.route(
            "GET",
            "/campaigns",
            Reply.html(
                page_html(
                    component_html(snapshot_json(name="synthetic.navigation", data={"open": False}))
                )
            ),
        )
        with self.assertRaises(OutcraftError):
            self.client(server).login(EMAIL, PASSWORD)
        self.assertTrue(server.http_reads())

    def test_external_form_action_is_blocked_before_credentials_leave_origin(self) -> None:
        with LocalServer() as foreign:
            foreign.route("POST", "/steal", Reply.json({"redirect": "/campaigns"}))
            server = self.server(form_action=foreign.base_url + "/steal")
            with self.assertRaises(OutcraftError) as caught:
                self.client(server).login(EMAIL, PASSWORD)
            self.assert_safe_error(caught.exception)
            self.assertEqual(foreign.requests, [])
            self.assertEqual(server.submissions, [])
            self.assertIn(
                "HeadlessChrome", server.requests_to("GET", "/auth/login")[0].headers["user-agent"]
            )

    def test_external_login_redirect_is_blocked_before_foreign_page_load(self) -> None:
        with LocalServer() as foreign:
            server = self.server()
            server.route("GET", "/auth/login", Reply.redirect(foreign.base_url + "/auth/login"))
            with self.assertRaises(OutcraftError) as caught:
                self.client(server).login(EMAIL, PASSWORD)
            self.assert_safe_error(caught.exception)
            self.assertEqual(foreign.requests, [])
            self.assertEqual(server.submissions, [])
            self.assertIn(
                "HeadlessChrome", server.requests_to("GET", "/auth/login")[0].headers["user-agent"]
            )

    def test_external_post_preserving_redirect_is_blocked_before_foreign_post(self) -> None:
        with LocalServer() as foreign:
            server = self.server()
            server.route("POST", "/auth/session", Reply.redirect(foreign.base_url + "/steal", 307))
            with self.assertRaises(OutcraftError) as caught:
                self.client(server).login(EMAIL, PASSWORD)
            self.assert_safe_error(caught.exception)
            self.assertEqual(len(server.requests_to("POST", "/auth/session")), 1)
            self.assertEqual(foreign.requests, [])

    def test_external_browser_navigation_after_login_is_blocked(self) -> None:
        with LocalServer() as foreign:
            foreign.route("GET", "/landing", Reply.html(campaign_page()))
            server = self.server(redirect=foreign.base_url + "/landing")
            with self.assertRaises(OutcraftError) as caught:
                self.client(server).login(EMAIL, PASSWORD)
            self.assert_safe_error(caught.exception)
            self.assertEqual(len(server.submissions), 1)
            self.assertEqual(foreign.requests, [])


if __name__ == "__main__":
    unittest.main()
