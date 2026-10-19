"""Campaign mutations resolve and verify records across exposed pagination only."""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlsplit

from _campaign_fixtures import campaign_page, list_snapshot, sent_component
from _fixtures import UPDATE_URI, update_body
from _support import LocalServer, Reply

from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport


def paginated_page(
    number: int,
    records: tuple[tuple[int, str, str], ...],
    *,
    next_control: str = "enabled",
) -> str:
    snapshot = list_snapshot(f"page-{number}", paginators={"page": number})
    source = campaign_page(records, snapshot)
    if next_control == "absent":
        return source
    disabled = " disabled" if next_control == "disabled" else ""
    control = f"<button wire:click=\"nextPage('page')\"{disabled}>Next</button>"
    return source.replace("</div><script", control + "</div><script", 1)


class CampaignServer(LocalServer):
    """Keep query-aware pages available through arbitrary safe re-fetches."""

    def __init__(self) -> None:
        super().__init__()
        self.pages: dict[int, str] = {}
        self.saved_pages: dict[int, str] = {}
        self.save_after = 2
        self.get_limit = 12
        self.get_count = 0
        self.post_count = 0

    def _next_reply(self, method: str, path: str) -> Reply:
        parsed = urlsplit(path)
        if method == "GET" and parsed.path == "/campaigns":
            self.get_count += 1
            if self.get_count > self.get_limit:
                return Reply(status=508, body="Synthetic pagination request limit exceeded")
            number = int(parse_qs(parsed.query).get("page", ["1"])[0])
            source = self.pages.get(number)
            return Reply.html(source) if source is not None else Reply(status=404)
        if method == "POST" and parsed.path == UPDATE_URI:
            self.post_count += 1
            if self.post_count == self.save_after:
                self.pages.update(self.saved_pages)
        return super()._next_reply(method, path)


class CampaignPaginationRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = CampaignServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))
        self.first = paginated_page(1, ((7, "First campaign", "Paused"),))
        self.server.pages = {
            1: self.first,
            2: paginated_page(2, ((19, "Second campaign", "Running"),), next_control="disabled"),
        }
        self.server.route(
            "POST", UPDATE_URI, Reply.json(update_body(list_snapshot("action-finished"), {}))
        )

    def rename_replies(self) -> None:
        mounted = list_snapshot(
            "mounted-page-2",
            mountedActions=[
                [{"name": "edit_name", "data": {"name": "Second campaign"}}],
                {"s": "arr"},
            ],
        )
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(update_body(mounted, {})),
            Reply.json(update_body(list_snapshot("saved-page-2"), {})),
        )

    def assert_page_two_mutation(self, action: str) -> None:
        posts = self.server.requests_to("POST", UPDATE_URI)
        self.assertGreaterEqual(len(posts), 1)
        sent = sent_component(posts[0])
        self.assertEqual(sent["snapshot"], list_snapshot("page-2", paginators={"page": 2}))
        self.assertEqual(sent["calls"][0]["method"], "mountAction")
        self.assertEqual(
            sent["calls"][0]["params"], [action, {}, {"recordKey": "19", "table": True}]
        )
        last_post = max(
            i for i, request in enumerate(self.server.requests) if request.method == "POST"
        )
        verified = [
            request.path
            for request in self.server.requests[last_post + 1 :]
            if request.method == "GET"
        ]
        self.assertIn("/campaigns?page=2", verified, "Persisted state must be checked on page 2")

    def test_page_two_rename_validates_target_then_verifies_the_renamed_record(self) -> None:
        self.rename_replies()
        self.server.saved_pages[2] = paginated_page(
            2, ((19, "Renamed second campaign", "Running"),), next_control="disabled"
        )
        result = self.client.rename(19, "Renamed second campaign", confirm=True)
        self.assertEqual(result, {"id": 19, "name": "Renamed second campaign", "status": "Running"})
        self.assert_page_two_mutation("edit_name")
        posts = self.server.requests_to("POST", UPDATE_URI)
        self.assertEqual(len(posts), 2)
        self.assertEqual(
            sent_component(posts[1])["updates"],
            {"mountedActions.0.data.name": "Renamed second campaign"},
        )

    def test_page_two_pause_verifies_the_status_on_the_target_page(self) -> None:
        self.server.save_after = 1
        self.server.saved_pages[2] = paginated_page(
            2, ((19, "Second campaign", "Paused"),), next_control="disabled"
        )
        result = self.client.set_running(19, False, confirm=True)
        self.assertEqual(result["status"], "Paused")
        self.assert_page_two_mutation("toggle_campaign")

    def test_page_two_resume_verifies_the_status_on_the_target_page(self) -> None:
        self.server.pages[2] = paginated_page(
            2, ((19, "Second campaign", "Paused"),), next_control="disabled"
        )
        self.server.save_after = 1
        self.server.saved_pages[2] = paginated_page(
            2, ((19, "Second campaign", "Running"),), next_control="disabled"
        )
        result = self.client.set_running(19, True, confirm=True)
        self.assertEqual(result["status"], "Running")
        self.assert_page_two_mutation("toggle_campaign")

    def test_unknown_rename_id_never_submits_a_mutation(self) -> None:
        self.rename_replies()
        with self.assertRaises(OutcraftError):
            self.client.rename(999, "Unknown campaign", confirm=True)
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])
        self.assertIn("/campaigns?page=2", [request.path for request in self.server.requests])

    def test_unknown_pause_id_checks_the_last_page_without_mutating(self) -> None:
        with self.assertRaises(OutcraftError):
            self.client.set_running(999, False, confirm=True)
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])
        self.assertEqual(
            [request.path for request in self.server.requests],
            ["/campaigns", "/campaigns?page=2"],
        )

    def test_page_two_rename_rejects_a_success_response_without_persistence(self) -> None:
        self.rename_replies()
        with self.assertRaises(OutcraftError):
            self.client.rename(19, "Not persisted", confirm=True)
        self.assertEqual(len(self.server.requests_to("POST", UPDATE_URI)), 2)
        self.assert_page_two_mutation("edit_name")

    def test_disabled_next_control_prevents_fetching_an_unexposed_page(self) -> None:
        self.server.pages[1] = paginated_page(
            1, ((7, "First campaign", "Paused"),), next_control="disabled"
        )
        with self.assertRaises(OutcraftError):
            self.client.set_running(19, False, confirm=True)
        self.assertEqual([request.path for request in self.server.requests], ["/campaigns"])

    def test_missing_next_control_prevents_guessing_another_page(self) -> None:
        self.server.pages[1] = paginated_page(
            1, ((7, "First campaign", "Paused"),), next_control="absent"
        )
        with self.assertRaises(OutcraftError):
            self.client.set_running(19, False, confirm=True)
        self.assertEqual([request.path for request in self.server.requests], ["/campaigns"])

    def test_repeated_page_terminates_without_guessing_until_the_server_limit(self) -> None:
        self.server.pages = {number: self.first for number in range(1, 13)}
        with self.assertRaises(OutcraftError):
            self.client.set_running(999, False, confirm=True)
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])
        paths = [request.path for request in self.server.requests]
        self.assertIn("/campaigns?page=2", paths)
        self.assertLessEqual(len(paths), 3, "Repeated page content must stop pagination")


if __name__ == "__main__":
    unittest.main()
