"""Contract: Livewire.call posts the exact snapshot to the discovered update URI and returns fresh state."""

from __future__ import annotations

import unittest

from _fixtures import (
    CAMPAIGNS_NAME,
    CSRF,
    UPDATE_URI,
    campaigns_page,
    component_html,
    page_html,
    snapshot_json,
    update_body,
)
from _support import LocalServer, Reply

from outcraft_cli.livewire import Livewire
from outcraft_cli.transport import Transport

FRESH_DATA = {"campaigns": [[{"id": 7, "name": "Renamed"}], {"s": "arr"}], "search": "q4"}
FRESHER_DATA = {"campaigns": [[], {"s": "arr"}], "search": "none"}


class LivewireCallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.original = snapshot_json()
        self.fresh = snapshot_json(data=FRESH_DATA, checksum="fresh-1")
        self.fresher = snapshot_json(data=FRESHER_DATA, checksum="fresh-2")
        self.server.route(
            "GET",
            "/campaigns",
            Reply.html(
                campaigns_page(),
                cookies=["XSRF-TOKEN=x1; Path=/", "outcraft_session=s1; Path=/; HttpOnly"],
            ),
        )
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(
                update_body(self.fresh, {"html": "<div>1</div>", "dispatches": []}),
                cookies=["outcraft_session=s2; Path=/"],
            ),
            Reply.json(update_body(self.fresher), cookies=["outcraft_session=s3; Path=/"]),
        )
        self.livewire = Livewire(Transport(base_url=self.server.base_url, session_file=None))

    def posts(self) -> list:
        return self.server.requests_to("POST", UPDATE_URI)

    def test_page_fetches_and_parses(self) -> None:
        page = self.livewire.page("/campaigns")
        self.assertEqual(page.csrf, CSRF)
        self.assertEqual(page.update_uri, UPDATE_URI)
        self.assertEqual([c.name for c in page.components], [CAMPAIGNS_NAME])
        self.assertEqual(page.components[0].snapshot, self.original)

    def test_call_posts_livewire_update_payload(self) -> None:
        page = self.livewire.page("/campaigns")
        self.livewire.call(page, page.components[0], "sortBy", params=["name"])

        self.assertEqual(len(self.posts()), 1)
        body = self.posts()[0].json()
        self.assertEqual(body["_token"], CSRF)
        self.assertEqual(len(body["components"]), 1)

        sent = body["components"][0]
        self.assertEqual(
            sent["snapshot"], self.original, "snapshot must be byte-for-byte the page attribute"
        )
        self.assertEqual(sent["updates"], {})
        self.assertEqual(len(sent["calls"]), 1)
        call = sent["calls"][0]
        self.assertEqual(call["method"], "sortBy")
        self.assertEqual(call["params"], ["name"])
        self.assertEqual(call["metadata"], {})

    def test_defaults_send_empty_params_and_updates(self) -> None:
        page = self.livewire.page("/campaigns")
        self.livewire.call(page, page.components[0], "refresh")

        sent = self.posts()[0].json()["components"][0]
        self.assertEqual(sent["calls"][0]["params"], [])
        self.assertEqual(sent["updates"], {})

    def test_updates_are_passed_through(self) -> None:
        page = self.livewire.page("/campaigns")
        self.livewire.call(
            page,
            page.components[0],
            "applyFilters",
            updates={"search": "q4", "filters.status": "paused"},
        )

        sent = self.posts()[0].json()["components"][0]
        self.assertEqual(sent["updates"], {"search": "q4", "filters.status": "paused"})

    def test_call_returns_component_from_fresh_snapshot(self) -> None:
        page = self.livewire.page("/campaigns")
        result = self.livewire.call(page, page.components[0], "rename", params=[7, "Renamed"])

        self.assertEqual(result.snapshot, self.fresh)
        self.assertEqual(result.name, CAMPAIGNS_NAME)
        self.assertEqual(result.data, {"campaigns": [{"id": 7, "name": "Renamed"}], "search": "q4"})
        self.assertEqual(result.effects, {"html": "<div>1</div>", "dispatches": []})

    def test_chained_call_sends_the_returned_snapshot(self) -> None:
        page = self.livewire.page("/campaigns")
        first = self.livewire.call(page, page.components[0], "rename", params=[7, "Renamed"])
        second = self.livewire.call(page, first, "clear")

        sent = [p.json()["components"][0]["snapshot"] for p in self.posts()]
        self.assertEqual(sent, [self.original, self.fresh])
        self.assertEqual(second.snapshot, self.fresher)
        self.assertEqual(second.data, {"campaigns": [], "search": "none"})

    def test_page_cookies_and_rotation_reach_update_posts(self) -> None:
        page = self.livewire.page("/campaigns")
        first = self.livewire.call(page, page.components[0], "rename")
        self.livewire.call(page, first, "clear")

        cookies = [p.cookies() for p in self.posts()]
        self.assertEqual(cookies[0], {"XSRF-TOKEN": "x1", "outcraft_session": "s1"})
        self.assertEqual(cookies[1]["outcraft_session"], "s2")

    def test_update_post_is_json(self) -> None:
        page = self.livewire.page("/campaigns")
        self.livewire.call(page, page.components[0], "refresh")
        self.assertTrue(
            self.posts()[0].headers.get("content-type", "").startswith("application/json")
        )

    def test_only_the_target_component_is_sent(self) -> None:
        nav = snapshot_json(name="nav.menu", component_id="lw-nav", data={"open": False})
        html = page_html(component_html(nav, component_id="lw-nav"), component_html(self.original))
        self.server.route("GET", "/two", Reply.html(html))
        page = self.livewire.page("/two")

        target = next(c for c in page.components if c.name == CAMPAIGNS_NAME)
        self.livewire.call(page, target, "refresh")

        components = self.posts()[0].json()["components"]
        self.assertEqual([c["snapshot"] for c in components], [self.original])


if __name__ == "__main__":
    unittest.main()
