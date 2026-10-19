"""Contract: Livewire fails closed on validation errors, bad responses, and unsafe pages."""

from __future__ import annotations

import unittest

from _fixtures import (
    CSRF,
    UPDATE_URI,
    campaigns_page,
    component_html,
    page_html,
    snapshot_json,
    update_body,
)
from _support import LocalServer, Reply

from outcraft_cli.errors import OutcraftError
from outcraft_cli.livewire import Livewire
from outcraft_cli.transport import Transport

SECRET = "sk_live_RESPONSESECRET_123"


class LivewireFailureBase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.server.route(
            "GET",
            "/campaigns",
            Reply.html(campaigns_page(), cookies=["outcraft_session=s1; Path=/"]),
        )
        self.livewire = Livewire(Transport(base_url=self.server.base_url, session_file=None))

    def posts(self, path: str = UPDATE_URI) -> list:
        return self.server.requests_to("POST", path)

    def call_with_reply(self, reply: Reply) -> OutcraftError:
        self.server.route("POST", UPDATE_URI, reply)
        page = self.livewire.page("/campaigns")
        with self.assertRaises(OutcraftError) as ctx:
            self.livewire.call(page, page.components[0], "save")
        return ctx.exception


class ValidationTest(LivewireFailureBase):
    def test_memo_errors_fail_the_call(self) -> None:
        errors = {
            "form.name": ["The name field is required."],
            "form.budget": ["Must be at least 10."],
        }
        error = self.call_with_reply(
            Reply.json(update_body(snapshot_json(errors=errors, checksum="v1")))
        )
        self.assertIn("form.name", str(error))

    def test_empty_errors_list_is_success(self) -> None:
        self.server.route(
            "POST", UPDATE_URI, Reply.json(update_body(snapshot_json(errors=[], checksum="ok")))
        )
        page = self.livewire.page("/campaigns")
        result = self.livewire.call(page, page.components[0], "save")
        self.assertIn('"checksum":"ok"', result.snapshot)

    def test_empty_errors_object_is_success(self) -> None:
        self.server.route(
            "POST", UPDATE_URI, Reply.json(update_body(snapshot_json(errors={}, checksum="ok")))
        )
        page = self.livewire.page("/campaigns")
        self.livewire.call(page, page.components[0], "save")


class MalformedResponseTest(LivewireFailureBase):
    def test_bad_update_responses_fail_closed(self) -> None:
        cases = {
            "empty body": Reply(status=200, body=""),
            "not json": Reply(status=200, body="definitely not json"),
            "html login page": Reply.html("<html><form action='/login'></form></html>"),
            "json array": Reply.json([]),
            "no components key": Reply.json({"assets": []}),
            "empty components": Reply.json({"components": [], "assets": []}),
            "components not a list": Reply.json({"components": {"snapshot": "{}"}}),
            "missing snapshot": Reply.json({"components": [{"effects": {}}]}),
            "null snapshot": Reply.json({"components": [{"snapshot": None, "effects": {}}]}),
            "snapshot not json": Reply.json(
                {"components": [{"snapshot": "{broken", "effects": {}}]}
            ),
            "snapshot not object": Reply.json({"components": [{"snapshot": "[]", "effects": {}}]}),
        }
        for label, reply in cases.items():
            with self.subTest(case=label):
                self.call_with_reply(reply)

    def test_page_expired_419_fails_once_without_retry(self) -> None:
        self.call_with_reply(Reply(status=419, body="Page Expired"))
        self.assertEqual(len(self.posts()), 1)

    def test_server_error_fails_once_and_hides_body(self) -> None:
        error = self.call_with_reply(
            Reply(status=500, body=f'{{"exception":"x","key":"{SECRET}"}}')
        )
        self.assertEqual(len(self.posts()), 1)
        rendered = f"{error} {error!r}"
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn(CSRF, rendered)

    def test_validation_422_fails_without_echoing_secrets(self) -> None:
        error = self.call_with_reply(
            Reply(status=422, body=f'{{"message":"bad","token":"{SECRET}"}}')
        )
        self.assertNotIn(SECRET, f"{error} {error!r}")


class UnsafePageTest(LivewireFailureBase):
    def setUp(self) -> None:
        super().setUp()
        self.other = LocalServer().__enter__()
        self.addCleanup(self.other.__exit__, None, None, None)
        self.other.route("POST", "/livewire/update", Reply.json(update_body(snapshot_json())))

    def assert_call_refused(self, html: str) -> None:
        self.server.route("GET", "/unsafe", Reply.html(html))
        self.server.route("POST", UPDATE_URI, Reply.json(update_body(snapshot_json())))
        with self.assertRaises(OutcraftError):
            page = self.livewire.page("/unsafe")
            self.livewire.call(page, page.components[0], "save")
        self.assertEqual(self.other.requests, [], "update reached a foreign origin")

    def test_external_update_uri_is_refused(self) -> None:
        component = component_html(snapshot_json())
        self.assert_call_refused(
            page_html(component, update_uri=f"{self.other.base_url}/livewire/update")
        )

    def test_protocol_relative_update_uri_is_refused(self) -> None:
        component = component_html(snapshot_json())
        self.assert_call_refused(
            page_html(component, update_uri=f"//{self.other.netloc}/livewire/update")
        )

    def test_missing_csrf_fails_before_posting(self) -> None:
        self.assert_call_refused(page_html(component_html(snapshot_json()), csrf=None))
        self.assertEqual(self.posts(), [])

    def test_missing_update_uri_fails_before_posting(self) -> None:
        self.assert_call_refused(page_html(component_html(snapshot_json()), update_uri=None))
        self.assertEqual(self.server.requests_to("POST", "/livewire/update"), [])
        self.assertEqual(self.posts(), [])


if __name__ == "__main__":
    unittest.main()
