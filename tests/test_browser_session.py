"""Browser-to-HTTPX session handoff, origin isolation, and atomic persistence."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from _browser_fixtures import without_browser
from _campaign_fixtures import campaign_page
from _fixtures import UPDATE_URI
from _support import LocalServer, Reply

from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport

USER_AGENT = "SyntheticBrowser/123.0"
IDENTITY = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-CH-UA": '"Chromium";v="123"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Linux"',
}


def browser_cookie(
    name: str = "outcraft_session", value: str = "browser-one", **extra: object
) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "domain": "127.0.0.1",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": False,
        "sameSite": "Lax",
        **extra,
    }


class BrowserSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.session_file = self.directory / "session.json"
        self.transport = Transport(base_url=self.server.base_url, session_file=self.session_file)
        self.server.route("GET", "/campaigns", Reply.html(campaign_page()))

    def adopt(
        self, cookies: list[dict[str, object]] | None = None, headers: dict[str, str] | None = None
    ) -> None:
        self.transport.adopt_browser_session(
            [browser_cookie()] if cookies is None else cookies,
            dict(IDENTITY) if headers is None else headers,
        )

    def test_adoption_filters_foreign_cookies_and_nonidentity_headers(self) -> None:
        unsafe = {
            "Authorization": "Bearer synthetic-stolen",
            "Cookie": "injected=synthetic-stolen",
            "Host": "foreign.invalid",
            "Origin": "https://foreign.invalid",
            "Referer": "https://foreign.invalid/private",
            "Content-Type": "application/x-malicious",
            "X-Livewire": "injected",
            "X-Api-Key": "synthetic-stolen",
            "Sec-Fetch-Site": "cross-site",
        }
        cookies = [
            browser_cookie(),
            browser_cookie("foreign", "reject", domain="foreign.invalid"),
            browser_cookie("parent", "reject", domain=".0.0.1"),
            browser_cookie("private", "path-only", path="/private"),
            browser_cookie("expired", "reject", expires=1),
            browser_cookie("https_only", "reject", secure=True),
        ]
        self.adopt(cookies, {**IDENTITY, **unsafe})
        for transport in (
            self.transport,
            Transport(base_url=self.server.base_url, session_file=self.session_file),
        ):
            transport.request("GET", "/campaigns")
            request = self.server.requests[-1]
            self.assertEqual(request.cookies(), {"outcraft_session": "browser-one"})
            for key, value in IDENTITY.items():
                self.assertEqual(request.headers.get(key.lower()), value)
            self.assertEqual(request.headers["host"], self.server.netloc)
            for key in unsafe:
                if key.lower() not in ("host", "cookie"):
                    self.assertNotIn(key.lower(), request.headers)
        self.assertNotIn("synthetic-stolen", self.session_file.read_text())
        self.server.route("GET", "/private/check", Reply.html("ok"))
        self.transport.request("GET", "/private/check")
        self.assertEqual(self.server.requests[-1].cookies().get("private"), "path-only")
        self.assertEqual(self.server.requests_to("POST", "/auth/login"), [])

    def test_json_write_keeps_transport_owned_headers_not_browser_injections(self) -> None:
        self.adopt(headers={**IDENTITY, "Origin": "https://foreign.invalid", "X-Livewire": "false"})
        self.server.route("POST", UPDATE_URI, Reply.json({}))
        self.transport.request("POST", UPDATE_URI, {"synthetic": True})
        request = self.server.requests[-1]
        self.assertEqual(request.headers["origin"], self.server.base_url)
        self.assertEqual(request.headers["referer"], self.server.base_url + "/")
        self.assertEqual(request.headers["x-livewire"], "true")
        self.assertTrue(request.headers["content-type"].startswith("application/json"))
        self.assertEqual(request.headers["user-agent"], USER_AGENT)

    def test_cookie_rotation_atomically_replaces_single_private_session_with_identity(self) -> None:
        self.adopt()
        self.assertTrue(self.session_file.is_file(), "Adoption must persist the complete session")
        self.assertEqual(stat.S_IMODE(self.session_file.stat().st_mode), 0o600)
        original = self.session_file.read_bytes()
        stored = json.loads(original)
        flattened = json.dumps(stored)
        for value in (self.server.base_url, USER_AGENT, "browser-one"):
            self.assertIn(value, flattened)
        self.server.route(
            "GET",
            "/rotate",
            Reply.html("rotated", cookies=["outcraft_session=browser-two; Path=/; HttpOnly"]),
        )
        with self.session_file.open("rb") as old_file:
            old_inode = os.fstat(old_file.fileno()).st_ino
            self.transport.request("GET", "/rotate")
            self.assertEqual(
                old_file.read(), original, "Session was rewritten in place, not atomically replaced"
            )
            self.assertNotEqual(self.session_file.stat().st_ino, old_inode)
        self.assertEqual(stat.S_IMODE(self.session_file.stat().st_mode), 0o600)
        restored = Transport(base_url=self.server.base_url, session_file=self.session_file)
        restored.request("GET", "/campaigns")
        self.assertEqual(self.server.requests[-1].cookies().get("outcraft_session"), "browser-two")
        for key, value in IDENTITY.items():
            self.assertEqual(self.server.requests[-1].headers.get(key.lower()), value)
        self.assertEqual(list(self.directory.iterdir()), [self.session_file])

    def test_reloading_browser_session_on_different_port_is_refused_before_http(self) -> None:
        self.adopt()
        with LocalServer() as foreign:
            foreign.route("GET", "/campaigns", Reply.html(campaign_page()))
            with self.assertRaises(OutcraftError):
                Transport(base_url=foreign.base_url, session_file=self.session_file).request(
                    "GET", "/campaigns"
                )
            self.assertEqual(foreign.requests, [])

    def test_same_origin_redirect_preserves_identity_but_foreign_redirect_leaks_nothing(
        self,
    ) -> None:
        self.adopt()
        self.server.route("GET", "/start", Reply.redirect("/campaigns"))
        self.transport.request("GET", "/start")
        self.assertEqual(self.server.requests[-1].cookies().get("outcraft_session"), "browser-one")
        self.assertEqual(self.server.requests[-1].headers.get("user-agent"), USER_AGENT)
        with LocalServer() as foreign:
            self.server.route("GET", "/escape", Reply.redirect(foreign.base_url + "/steal", 307))
            with self.assertRaises(OutcraftError):
                self.transport.request("GET", "/escape")
            self.assertEqual(foreign.requests, [])

    def test_existing_session_authentication_is_http_only_even_with_credentials_available(
        self,
    ) -> None:
        self.adopt()
        result = without_browser(
            self.server.base_url,
            'client.ensure_authenticated("synthetic@example.invalid", "synthetic-password"); result = client.status()',
            self.session_file,
        )
        self.assertEqual(result, {"authenticated": True, "origin": self.server.base_url})
        self.assertTrue(self.server.requests_to("GET", "/campaigns"))
        self.assertEqual(self.server.requests_to("GET", "/auth/login"), [])
        for request in self.server.requests:
            self.assertEqual(request.cookies().get("outcraft_session"), "browser-one")
            self.assertEqual(request.headers.get("user-agent"), USER_AGENT)

    def test_failed_campaign_post_is_never_retried_or_reauthenticated(self) -> None:
        self.adopt()
        self.server.route("POST", UPDATE_URI, Reply(status=419, body="Session expired"))
        with self.assertRaises(OutcraftError):
            Client(self.transport).create("Synthetic campaign", confirm=True)
        self.assertEqual(len(self.server.requests_to("POST", UPDATE_URI)), 1)
        self.assertEqual(self.server.requests_to("GET", "/auth/login"), [])
        self.assertEqual(self.server.requests_to("POST", "/auth/session"), [])

    def test_adopting_replacement_session_drops_old_browser_cookies(self) -> None:
        self.adopt([browser_cookie(), browser_cookie("old_identity", "old")])
        self.adopt([browser_cookie(value="replacement")], {"user-agent": "ReplacementBrowser/124"})
        self.transport.request("GET", "/campaigns")
        self.assertEqual(self.server.requests[-1].cookies(), {"outcraft_session": "replacement"})
        self.assertEqual(
            self.server.requests[-1].headers.get("user-agent"), "ReplacementBrowser/124"
        )
        for key in IDENTITY:
            if key.lower() != "user-agent":
                self.assertNotIn(key.lower(), self.server.requests[-1].headers)


if __name__ == "__main__":
    unittest.main()
