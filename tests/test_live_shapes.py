"""Snapshot shapes seen on the authenticated dashboard, rebuilt synthetically."""

from __future__ import annotations

import json
import unittest

from _campaign_fixtures import LIST_NAME, campaign_page
from _fixtures import UPDATE_URI, component_html, page_html, snapshot_json, update_body
from _support import LocalServer, Reply

from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.html import Component, parse_page
from outcraft_cli.livewire import Livewire
from outcraft_cli.transport import Transport
from outcraft_cli.views import describe

BELL_NAME = "synthetic.notification-bell"


def raw_snapshot(data: object, name: str = BELL_NAME, component_id: str = "bell") -> str:
    """PHP serializes an empty associative array as [], not {}."""
    payload = {
        "data": data,
        "memo": {"id": component_id, "name": name, "path": "campaigns", "errors": []},
        "checksum": "synthetic-bell",
    }
    return json.dumps(payload, separators=(",", ":")).replace("/", "\\/")


def bell_html() -> str:
    return component_html(raw_snapshot([]), component_id="bell")


def dashboard_with_bell() -> str:
    """Bell component first, then the usual navigation and campaign list."""
    page = campaign_page(((7, "Synthetic campaign", "Paused"),))
    return page.replace("<body>", "<body>" + bell_html(), 1)


class EmptyPhpArrayDataTest(unittest.TestCase):
    def test_empty_array_data_is_an_empty_mapping(self) -> None:
        component = Component(raw_snapshot([]))
        self.assertEqual(component.data, {})
        self.assertIsInstance(component.data, dict)
        self.assertEqual(component.name, BELL_NAME)
        self.assertEqual(component.memo["id"], "bell")

    def test_snapshot_bytes_are_preserved_for_resubmission(self) -> None:
        snapshot = raw_snapshot([])
        self.assertEqual(Component(snapshot).snapshot, snapshot)

    def test_nonempty_array_data_is_still_rejected(self) -> None:
        for data in ([1], [{"open": False}], ["text"], [[]]):
            with self.subTest(data=data), self.assertRaises(OutcraftError):
                Component(raw_snapshot(data))

    def test_scalar_or_null_data_is_still_rejected(self) -> None:
        for data in (None, "text", 0, True):
            with self.subTest(data=data), self.assertRaises(OutcraftError):
                Component(raw_snapshot(data))

    def test_empty_object_data_still_works(self) -> None:
        self.assertEqual(Component(raw_snapshot({})).data, {})

    def test_describe_accepts_an_empty_data_component(self) -> None:
        described = describe(Component(raw_snapshot([])))
        self.assertEqual(described["name"], BELL_NAME)
        self.assertEqual(described["data"], {})


class DashboardWithBellTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))

    def test_page_parses_bell_and_keeps_document_order(self) -> None:
        page = parse_page(dashboard_with_bell())
        self.assertEqual(
            [c.name for c in page.components],
            [BELL_NAME, "synthetic.navigation", LIST_NAME],
        )
        self.assertEqual(page.components[0].data, {})

    def test_list_component_is_still_selected_by_key(self) -> None:
        page = parse_page(dashboard_with_bell())
        self.assertEqual(page.component(key="tableSearch").name, LIST_NAME)

    def test_campaigns_list_reads_cards_next_to_bell(self) -> None:
        self.server.route("GET", "/campaigns", Reply.html(dashboard_with_bell()))
        result = self.client.campaigns()
        self.assertEqual(
            [(r["id"], r["name"], r["status"]) for r in result],
            [(7, "Synthetic campaign", "Paused")],
        )
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])

    def test_ensure_authenticated_does_not_log_in_again(self) -> None:
        self.server.route("GET", "/campaigns", Reply.html(dashboard_with_bell()))
        self.client.ensure_authenticated()
        self.assertEqual(self.server.requests_to("GET", "/auth/login"), [])
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])

    def test_inspect_describes_the_bell_component(self) -> None:
        self.server.route("GET", "/campaigns", Reply.html(dashboard_with_bell()))
        result = self.client.inspect_path("/campaigns")
        names = [c["name"] for c in result["components"]]
        self.assertIn(BELL_NAME, names)
        bell = result["components"][names.index(BELL_NAME)]
        self.assertEqual(bell["data"], {})


class LoginWithValidSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from _browser_fixtures import require_chromium

        require_chromium()

    def test_login_page_redirecting_to_campaigns_reports_authenticated_without_post(self) -> None:
        with LocalServer() as server:
            server.route("GET", "/auth/login", Reply.redirect("/campaigns"))
            server.route("GET", "/campaigns", Reply.html(dashboard_with_bell()))
            client = Client(Transport(base_url=server.base_url, session_file=None))
            result = client.login("synthetic@example.invalid", "synthetic-password-not-a-secret")
        self.assertIs(result["authenticated"], True)
        self.assertEqual(result["origin"], server.base_url)
        self.assertEqual(server.requests_to("POST", UPDATE_URI), [])
        self.assertNotIn("synthetic-password-not-a-secret", str(result))


class LivewireReplyWithEmptyDataTest(unittest.TestCase):
    def test_update_reply_with_empty_array_data_is_accepted(self) -> None:
        with LocalServer() as server:
            server.route("GET", "/campaigns", Reply.html(page_html(bell_html())))
            server.route("POST", UPDATE_URI, Reply.json(update_body(raw_snapshot([]), {})))
            livewire = Livewire(Transport(base_url=server.base_url, session_file=None))
            page = livewire.page("/campaigns")
            result = livewire.call(page, page.components[0], "refresh")
        self.assertEqual(result.data, {})
        self.assertEqual(result.name, BELL_NAME)

    def test_update_reply_with_nonempty_array_data_fails_closed(self) -> None:
        with LocalServer() as server:
            server.route("GET", "/campaigns", Reply.html(page_html(bell_html())))
            server.route("POST", UPDATE_URI, Reply.json(update_body(raw_snapshot([1]), {})))
            livewire = Livewire(Transport(base_url=server.base_url, session_file=None))
            page = livewire.page("/campaigns")
            with self.assertRaises(OutcraftError):
                livewire.call(page, page.components[0], "refresh")

    def test_fixture_helper_still_builds_object_data(self) -> None:
        # Guards the synthetic helper: object data must not be mistaken for the bell shape.
        self.assertEqual(Component(snapshot_json(data={"open": False})).data, {"open": False})


if __name__ == "__main__":
    unittest.main()
