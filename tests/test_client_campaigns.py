"""Campaign listing and confirmed actions follow the real DOM and mounted-action flow."""

from __future__ import annotations

import unittest

from _campaign_fixtures import campaign_page, list_snapshot, request_order, sent_component
from _fixtures import UPDATE_URI, update_body
from _support import LocalServer, Reply

from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport


class ClientCampaignsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))
        self.server.route("GET", "/campaigns", Reply.html(campaign_page()))

    def action_replies(self, effects: dict | None = None) -> str:
        mounted = list_snapshot(
            "mounted-fresh",
            mountedActions=[
                [{"name": "synthetic-mounted-action", "data": {"name": "Synthetic campaign"}}],
                {"s": "arr"},
            ],
        )
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(update_body(mounted)),
            Reply.json(update_body(list_snapshot("saved-fresh"), effects or {})),
        )
        return mounted

    def assert_action(self, params: list, name: str, mounted: str) -> None:
        posts = self.server.requests_to("POST", UPDATE_URI)
        self.assertEqual(len(posts), 2)
        first, second = [sent_component(post) for post in posts]
        self.assertEqual(first["snapshot"], list_snapshot())
        self.assertEqual(first["updates"], {})
        self.assertEqual(first["calls"][0]["method"], "mountAction")
        self.assertEqual(first["calls"][0]["params"], params)
        self.assertEqual(
            second["snapshot"], mounted, "save must use the newly signed mounted snapshot"
        )
        self.assertEqual(second["updates"], {"mountedActions.0.data.name": name})
        self.assertEqual(second["calls"][0]["method"], "callMountedAction")
        self.assertEqual(second["calls"][0]["params"], [])
        self.assertEqual(
            request_order(self.server),
            [
                ("GET", "/campaigns"),
                ("POST", UPDATE_URI),
                ("POST", UPDATE_URI),
                ("GET", "/campaigns"),
            ],
        )

    def test_campaigns_reads_cards_not_a_snapshot_campaigns_array(self) -> None:
        self.server.route(
            "GET",
            "/campaigns",
            Reply.html(
                campaign_page(
                    (
                        (7, "Synthetic Café & partners", "Paused"),
                        (12, "Second campaign", "Draft"),
                    )
                )
            ),
        )
        result = self.client.campaigns()
        self.assertIsInstance(result, list)
        self.assertEqual(
            [(row["id"], row["name"], row["status"]) for row in result],
            [
                (7, "Synthetic Café & partners", "Paused"),
                (12, "Second campaign", "Draft"),
            ],
        )
        self.assertTrue(all(isinstance(row["id"], int) for row in result))
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])

    def test_empty_campaigns_is_an_empty_list(self) -> None:
        self.server.route("GET", "/campaigns", Reply.html(campaign_page(())))
        self.assertEqual(self.client.campaigns(), [])

    def test_rename_mounts_saves_fresh_snapshot_then_verifies(self) -> None:
        name = "Renamed Café & partners"
        self.server.route(
            "GET",
            "/campaigns",
            Reply.html(campaign_page()),
            Reply.html(campaign_page(((7, name, "Paused"),))),
        )
        mounted = self.action_replies()
        result = self.client.rename(7, name, confirm=True)
        self.assert_action(["edit_name", {}, {"recordKey": "7", "table": True}], name, mounted)
        self.assertEqual(result["id"], 7)
        self.assertEqual(result["name"], name)

    def test_rename_rejects_success_response_when_name_did_not_persist(self) -> None:
        self.action_replies()
        with self.assertRaises(OutcraftError):
            self.client.rename(7, "Not persisted", confirm=True)
        self.assertEqual(len(self.server.requests_to("GET", "/campaigns")), 2)

    def test_create_uses_redirect_id_and_verifies_the_new_record(self) -> None:
        name = "Created campaign"
        self.server.route(
            "GET",
            "/campaigns",
            Reply.html(campaign_page()),
            Reply.html(
                campaign_page(
                    (
                        (7, "Synthetic campaign", "Paused"),
                        (8, name, "Draft"),
                    )
                )
            ),
        )
        mounted = self.action_replies({"redirect": "/campaigns/8/onboarding"})
        result = self.client.create(name, confirm=True)
        self.assert_action(["createNewCampaign"], name, mounted)
        self.assertEqual(result["id"], 8)
        self.assertEqual(result["name"], name)

    def test_create_does_not_infer_id_from_a_duplicate_name(self) -> None:
        name = "Synthetic campaign"
        self.action_replies({"redirect": "/campaigns/8/onboarding"})
        with self.assertRaises(OutcraftError):
            self.client.create(name, confirm=True)
        self.assertEqual(len(self.server.requests_to("GET", "/campaigns")), 2)

    def test_mutations_without_confirmation_fail_before_any_request(self) -> None:
        for operation in (
            lambda: self.client.rename(7, "Valid name"),
            lambda: self.client.create("Valid name"),
        ):
            with self.subTest(operation=operation), self.assertRaises(OutcraftError):
                operation()
            self.assertEqual(self.server.requests, [])

    def test_invalid_name_lengths_fail_before_any_request(self) -> None:
        for name in ("", "ab", "x" * 192):
            for operation in (
                lambda name=name: self.client.rename(7, name, confirm=True),
                lambda name=name: self.client.create(name, confirm=True),
            ):
                with self.subTest(length=len(name)), self.assertRaises(OutcraftError):
                    operation()
                self.assertEqual(self.server.requests, [])

    def test_name_length_boundaries_are_accepted_for_both_actions(self) -> None:
        for length in (3, 191):
            for action in ("rename", "create"):
                with self.subTest(length=length, action=action):
                    name = "x" * length
                    identifier = 7 if action == "rename" else 8
                    self.server.route(
                        "GET",
                        "/campaigns",
                        Reply.html(campaign_page()),
                        Reply.html(campaign_page(((identifier, name, "Draft"),))),
                    )
                    self.action_replies({"redirect": "/campaigns/8/onboarding"})
                    if action == "rename":
                        result = self.client.rename(7, name, confirm=True)
                    else:
                        result = self.client.create(name, confirm=True)
                    self.assertEqual(result["name"], name)


if __name__ == "__main__":
    unittest.main()
