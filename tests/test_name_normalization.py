"""Campaign writes and dry runs share the name the server will persist."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from _campaign_fixtures import campaign_page, list_snapshot, request_order, sent_component
from _fixtures import UPDATE_URI, update_body
from _support import LocalServer, Reply

from outcraft_cli.cli import main
from outcraft_cli.client import Client
from outcraft_cli.transport import Transport


class NameNormalizationTest(unittest.TestCase):
    def prepare_action(self, server: LocalServer, identifier: int) -> Client:
        server.route(
            "GET",
            "/campaigns",
            Reply.html(campaign_page()),
            Reply.html(campaign_page(((identifier, "Q4 outreach", "Draft"),))),
        )
        mounted = list_snapshot(
            "mounted-fresh",
            mountedActions=[
                [{"name": "synthetic-mounted-action", "data": {"name": ""}}],
                {"s": "arr"},
            ],
        )
        server.route(
            "POST",
            UPDATE_URI,
            Reply.json(update_body(mounted)),
            Reply.json(
                update_body(
                    list_snapshot("saved-fresh"),
                    {"redirect": f"/campaigns/{identifier}/onboarding"},
                )
            ),
        )
        return Client(Transport(base_url=server.base_url, session_file=None))

    def assert_canonical_write(self, server: LocalServer) -> None:
        posts = server.requests_to("POST", UPDATE_URI)
        self.assertEqual(len(posts), 2)
        self.assertEqual(
            sent_component(posts[1])["updates"],
            {"mountedActions.0.data.name": "Q4 outreach"},
        )
        self.assertEqual(
            request_order(server),
            [
                ("GET", "/campaigns"),
                ("POST", UPDATE_URI),
                ("POST", UPDATE_URI),
                ("GET", "/campaigns"),
            ],
        )

    def test_create_normalizes_written_name_and_accepts_canonical_readback(self) -> None:
        with LocalServer() as server:
            client = self.prepare_action(server, 8)
            result = client.create("  Q4  outreach  ", confirm=True)
            self.assert_canonical_write(server)
            self.assertEqual((result["id"], result["name"]), (8, "Q4 outreach"))

    def test_rename_normalizes_written_name_and_accepts_canonical_readback(self) -> None:
        with LocalServer() as server:
            client = self.prepare_action(server, 7)
            result = client.rename(7, "  Q4  outreach  ", confirm=True)
            self.assert_canonical_write(server)
            self.assertEqual((result["id"], result["name"]), (7, "Q4 outreach"))

    def test_rename_dry_run_reports_canonical_name_without_network(self) -> None:
        output = io.StringIO()
        with (
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No network")),
            redirect_stdout(output),
        ):
            result = main(["campaigns", "rename", "7", "  Q4  outreach  ", "--dry-run"])
        self.assertEqual(result, 0)
        plan = json.loads(output.getvalue())
        self.assertIs(plan["dry_run"], True)
        self.assertEqual(plan["name"], "Q4 outreach")


if __name__ == "__main__":
    unittest.main()
