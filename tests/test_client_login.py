"""Login requires a verified authenticated page, never just a successful POST."""

from __future__ import annotations

import unittest

from _campaign_fixtures import (
    campaign_page,
    login_page,
    login_snapshot,
    request_order,
    sent_component,
)
from _fixtures import UPDATE_URI, component_html, page_html, update_body
from _support import LocalServer, Reply

from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport

EMAIL = "synthetic@example.invalid"
PASSWORD = "synthetic-password-not-a-secret"


class ClientLoginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))
        self.server.route("GET", "/auth/login", Reply.html(login_page()))

    def reply(self, effects: dict) -> None:
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(
                update_body(login_snapshot(checksum="authenticated"), effects),
                cookies=["outcraft_session=synthetic; Path=/; HttpOnly"],
            ),
        )

    def test_login_selects_credentials_component_and_verifies_campaigns(self) -> None:
        self.reply({"redirect": "/after-login"})
        self.server.route("GET", "/after-login", Reply.html("Signed in"))
        self.server.route("GET", "/campaigns", Reply.html(campaign_page()))

        result = self.client.login(EMAIL, PASSWORD)

        self.assertIsInstance(result, dict)
        self.assertEqual(
            request_order(self.server),
            [
                ("GET", "/auth/login"),
                ("POST", UPDATE_URI),
                ("GET", "/after-login"),
                ("GET", "/campaigns"),
            ],
        )
        sent = sent_component(self.server.requests_to("POST", UPDATE_URI)[0])
        self.assertEqual(sent["snapshot"], login_snapshot())
        self.assertEqual(
            sent["updates"], {"email": EMAIL, "password": PASSWORD, "rememberMe": True}
        )
        self.assertEqual(sent["calls"][0]["method"], "authenticate")
        self.assertEqual(sent["calls"][0]["params"], [])
        verified = self.server.requests_to("GET", "/campaigns")[0]
        self.assertEqual(verified.cookies().get("outcraft_session"), "synthetic")
        self.assertNotIn(PASSWORD, str(result))

    def test_login_redirect_to_campaigns_is_verified(self) -> None:
        self.reply({"redirect": "/campaigns"})
        self.server.route("GET", "/campaigns", Reply.html(campaign_page()))
        self.assertIsInstance(self.client.login(EMAIL, PASSWORD), dict)
        self.assertTrue(self.server.requests_to("GET", "/campaigns"))

    def test_successful_post_does_not_count_as_authenticated(self) -> None:
        self.reply({"redirect": "/campaigns"})
        self.server.route("GET", "/campaigns", Reply.redirect("/auth/login"))
        with self.assertRaises(OutcraftError) as caught:
            self.client.login(EMAIL, PASSWORD)
        self.assertNotIn(EMAIL, str(caught.exception))
        self.assertNotIn(PASSWORD, str(caught.exception))

    def test_external_effect_redirect_is_rejected(self) -> None:
        with LocalServer() as foreign:
            foreign.route("GET", "/landing", Reply.html(campaign_page()))
            self.reply({"redirect": foreign.base_url + "/landing"})
            with self.assertRaises(OutcraftError):
                self.client.login(EMAIL, PASSWORD)
            self.assertEqual(foreign.requests, [])

    def test_ambiguous_login_component_fails_before_post(self) -> None:
        duplicate = component_html(login_snapshot(component_id="other"), component_id="other")
        self.server.route(
            "GET",
            "/auth/login",
            Reply.html(
                page_html(
                    component_html(login_snapshot()),
                    duplicate,
                )
            ),
        )
        with self.assertRaises(OutcraftError):
            self.client.login(EMAIL, PASSWORD)
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])

    def test_validation_error_does_not_expose_submitted_credentials(self) -> None:
        errors = {"password": [f"Rejected {EMAIL} / {PASSWORD}"]}
        self.server.route(
            "POST", UPDATE_URI, Reply.json(update_body(login_snapshot(errors=errors)))
        )
        with self.assertRaises(OutcraftError) as caught:
            self.client.login(EMAIL, PASSWORD)
        self.assertNotIn(EMAIL, str(caught.exception))
        self.assertNotIn(PASSWORD, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
