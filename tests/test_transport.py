"""Contract: Transport sends requests, keeps rotated cookies, and persists them safely."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from _support import LocalServer, Reply

from outcraft_cli.transport import Transport

SESSION_1 = "sess-one-111"
SESSION_2 = "sess-two-222"
SESSION_3 = "sess-three-333"


def session_cookie(value: str) -> str:
    return f"outcraft_session={value}; Path=/; HttpOnly; SameSite=Lax"


class TransportRequestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)

    def transport(self, **kwargs: object) -> Transport:
        return Transport(base_url=self.server.base_url, session_file=None, **kwargs)

    def test_default_construction_needs_no_network(self) -> None:
        Transport()
        Transport(session_file=None, timeout=5)

    def test_get_returns_status_text_and_final_url(self) -> None:
        self.server.route("GET", "/campaigns", Reply.json({"ok": True}))
        response = self.transport().request("GET", "/campaigns")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, '{"ok": true}')
        self.assertIsInstance(response.text, str)
        self.assertEqual(response.url, f"{self.server.base_url}/campaigns")

    def test_query_string_is_preserved(self) -> None:
        self.server.route("GET", "/campaigns", Reply.html("ok"))
        self.transport().request("GET", "/campaigns?page=2&status=active")
        self.assertEqual(self.server.requests[-1].path, "/campaigns?page=2&status=active")

    def test_payload_is_sent_as_json(self) -> None:
        self.server.route("POST", "/livewire/update", Reply.json({}))
        payload = {"_token": "t", "components": [{"snapshot": '{"a":"\\/"}', "updates": {}}]}
        self.transport().request("POST", "/livewire/update", payload)

        sent = self.server.requests[-1]
        self.assertEqual(sent.method, "POST")
        self.assertTrue(sent.headers.get("content-type", "").startswith("application/json"))
        self.assertEqual(sent.json(), payload)

    def test_same_origin_redirect_is_followed(self) -> None:
        self.server.route("GET", "/start", Reply.redirect("/final"))
        self.server.route("GET", "/final", Reply.html("landed"))
        response = self.transport().request("GET", "/start")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "landed")
        self.assertEqual(response.url, f"{self.server.base_url}/final")

    def test_cookie_rotation_carries_to_the_next_request(self) -> None:
        self.server.route(
            "GET",
            "/step",
            Reply.html("1", cookies=["XSRF-TOKEN=x1; Path=/", session_cookie(SESSION_1)]),
            Reply.html("2", cookies=[session_cookie(SESSION_2)]),
            Reply.html("3", cookies=[session_cookie(SESSION_3)]),
        )
        transport = self.transport()
        for _ in range(4):
            transport.request("GET", "/step")

        sent = [r.cookies() for r in self.server.requests]
        self.assertEqual(sent[0], {})
        self.assertEqual(sent[1], {"XSRF-TOKEN": "x1", "outcraft_session": SESSION_1})
        self.assertEqual(sent[2]["outcraft_session"], SESSION_2)
        self.assertEqual(sent[3]["outcraft_session"], SESSION_3)
        self.assertEqual(sent[3]["XSRF-TOKEN"], "x1")

    def test_cookies_set_on_redirect_are_kept(self) -> None:
        login = Reply.redirect("/home")
        login.headers.append(("Set-Cookie", session_cookie(SESSION_1)))
        self.server.route("GET", "/login", login)
        self.server.route("GET", "/home", Reply.html("home"))
        self.transport().request("GET", "/login")

        self.assertEqual(
            self.server.requests_to("GET", "/home")[0].cookies().get("outcraft_session"), SESSION_1
        )

    def test_expired_cookie_is_dropped(self) -> None:
        self.server.route(
            "GET",
            "/step",
            Reply.html("in", cookies=[session_cookie(SESSION_1)]),
            Reply.html(
                "out",
                cookies=["outcraft_session=deleted; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT"],
            ),
            Reply.html("anon"),
        )
        transport = self.transport()
        for _ in range(3):
            transport.request("GET", "/step")
        self.assertNotIn("outcraft_session", self.server.requests[2].cookies())


class SessionFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.session_file = Path(tmp.name) / "session.json"

    def test_rotated_cookie_survives_a_new_transport(self) -> None:
        self.server.route(
            "GET",
            "/step",
            Reply.html("1", cookies=[session_cookie(SESSION_1)]),
            Reply.html("2", cookies=[session_cookie(SESSION_2)]),
            Reply.html("3"),
        )
        first = Transport(base_url=self.server.base_url, session_file=self.session_file)
        first.request("GET", "/step")
        first.request("GET", "/step")

        second = Transport(base_url=self.server.base_url, session_file=self.session_file)
        second.request("GET", "/step")

        self.assertEqual(self.server.requests[-1].cookies().get("outcraft_session"), SESSION_2)

    def test_session_file_is_private_to_the_owner(self) -> None:
        self.server.route("GET", "/step", Reply.html("1", cookies=[session_cookie(SESSION_1)]))
        Transport(base_url=self.server.base_url, session_file=self.session_file).request(
            "GET", "/step"
        )

        self.assertTrue(self.session_file.exists())
        mode = stat.S_IMODE(os.stat(self.session_file).st_mode)
        self.assertEqual(mode & 0o077, 0, f"session file mode {oct(mode)} is readable by others")

    def test_missing_session_file_starts_empty(self) -> None:
        self.server.route("GET", "/step", Reply.html("1"))
        Transport(base_url=self.server.base_url, session_file=self.session_file).request(
            "GET", "/step"
        )
        self.assertEqual(self.server.requests[-1].cookies(), {})


if __name__ == "__main__":
    unittest.main()
