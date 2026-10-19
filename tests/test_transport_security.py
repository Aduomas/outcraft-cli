"""Contract: Transport stays on one origin, never retries POST, and fails without leaking secrets."""

from __future__ import annotations

import unittest

from _support import LocalServer, Reply, unused_local_url

from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport

BODY_SECRET = "sk_live_BODYSECRET_9d8c7b"
COOKIE_SECRET = "COOKIESECRET_4e5f6a"
ERROR_STATUSES = (401, 403, 419, 422, 429, 500)


class ServerPair(unittest.TestCase):
    """`server` is the app origin; `other` is a different origin (same host, other port)."""

    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.other = LocalServer().__enter__()
        self.addCleanup(self.other.__exit__, None, None, None)
        self.other.route("GET", "/steal", Reply.html("stolen"))
        self.other.route("POST", "/steal", Reply.html("stolen"))

    def transport(self, **kwargs: object) -> Transport:
        return Transport(base_url=self.server.base_url, session_file=None, **kwargs)

    def assert_other_untouched(self) -> None:
        self.assertEqual(self.other.requests, [], "request reached a foreign origin")


class OriginTest(ServerPair):
    def test_rejects_absolute_cross_origin_path(self) -> None:
        with self.assertRaises(OutcraftError):
            self.transport().request("GET", f"{self.other.base_url}/steal")
        self.assert_other_untouched()

    def test_rejects_protocol_relative_path(self) -> None:
        with self.assertRaises(OutcraftError):
            self.transport().request("GET", f"//{self.other.netloc}/steal")
        self.assert_other_untouched()

    def test_rejects_cross_origin_post_before_sending(self) -> None:
        with self.assertRaises(OutcraftError):
            self.transport().request("POST", f"{self.other.base_url}/steal", {"_token": "t"})
        self.assert_other_untouched()

    def test_rejects_cross_origin_redirect(self) -> None:
        self.server.route("GET", "/go", Reply.redirect(f"{self.other.base_url}/steal"))
        with self.assertRaises(OutcraftError):
            self.transport().request("GET", "/go")
        self.assert_other_untouched()

    def test_rejects_protocol_relative_redirect(self) -> None:
        self.server.route("GET", "/go", Reply.redirect(f"//{self.other.netloc}/steal"))
        with self.assertRaises(OutcraftError):
            self.transport().request("GET", "/go")
        self.assert_other_untouched()

    def test_cookies_never_reach_a_redirect_target_on_another_origin(self) -> None:
        self.server.route(
            "GET", "/login", Reply.html("in", cookies=[f"outcraft_session={COOKIE_SECRET}; Path=/"])
        )
        self.server.route("GET", "/go", Reply.redirect(f"{self.other.base_url}/steal", status=307))
        transport = self.transport()
        transport.request("GET", "/login")
        with self.assertRaises(OutcraftError):
            transport.request("GET", "/go")
        self.assert_other_untouched()

    def test_rejects_plain_http_base_url_off_loopback(self) -> None:
        with self.assertRaises(OutcraftError):
            Transport(base_url="http://app.outcraft.ai", session_file=None)


class NoRetryTest(ServerPair):
    def test_post_is_sent_exactly_once_on_failure(self) -> None:
        for status in (429, 500, 502, 503):
            with self.subTest(status=status):
                path = f"/fail-{status}"
                self.server.route("POST", path, Reply(status=status, body="nope"))
                with self.assertRaises(OutcraftError):
                    self.transport().request("POST", path, {"_token": "t"})
                self.assertEqual(len(self.server.requests_to("POST", path)), 1)

    def test_post_is_sent_exactly_once_on_timeout(self) -> None:
        self.server.route("POST", "/slow", Reply(status=200, body="late", delay=1.5))
        with self.assertRaises(OutcraftError):
            self.transport(timeout=0.3).request("POST", "/slow", {"_token": "t"})
        self.assertEqual(len(self.server.requests_to("POST", "/slow")), 1)


class ErrorSafetyTest(ServerPair):
    def test_error_statuses_raise_without_echoing_secrets(self) -> None:
        for status in ERROR_STATUSES:
            with self.subTest(status=status):
                path = f"/err-{status}"
                body = f'{{"message":"boom","token":"{BODY_SECRET}","trace":"{COOKIE_SECRET}"}}'
                reply = Reply(
                    status=status, body=body, headers=[("Content-Type", "application/json")]
                )
                reply.headers.append(("Set-Cookie", f"outcraft_session={COOKIE_SECRET}; Path=/"))
                self.server.route("GET", path, reply)

                with self.assertRaises(OutcraftError) as ctx:
                    self.transport().request("GET", path)

                rendered = f"{ctx.exception} {ctx.exception!r} {ctx.exception.args!r}"
                self.assertIn(str(status), str(ctx.exception))
                self.assertNotIn(BODY_SECRET, rendered)
                self.assertNotIn(COOKIE_SECRET, rendered)

    def test_error_does_not_echo_request_payload_or_cookies(self) -> None:
        self.server.route(
            "GET", "/login", Reply.html("in", cookies=[f"outcraft_session={COOKIE_SECRET}; Path=/"])
        )
        self.server.route("POST", "/save", Reply(status=422, body="invalid"))
        transport = self.transport()
        transport.request("GET", "/login")

        with self.assertRaises(OutcraftError) as ctx:
            transport.request("POST", "/save", {"_token": BODY_SECRET})

        rendered = f"{ctx.exception} {ctx.exception!r}"
        self.assertNotIn(BODY_SECRET, rendered)
        self.assertNotIn(COOKIE_SECRET, rendered)

    def test_errors_are_outcraft_errors(self) -> None:
        self.assertTrue(issubclass(OutcraftError, Exception))

    def test_connection_refused_becomes_outcraft_error(self) -> None:
        with self.assertRaises(OutcraftError):
            Transport(base_url=unused_local_url(), session_file=None, timeout=2).request("GET", "/")

    def test_timeout_becomes_outcraft_error(self) -> None:
        self.server.route("GET", "/slow", Reply(status=200, body="late", delay=1.5))
        with self.assertRaises(OutcraftError):
            self.transport(timeout=0.3).request("GET", "/slow")


if __name__ == "__main__":
    unittest.main()
