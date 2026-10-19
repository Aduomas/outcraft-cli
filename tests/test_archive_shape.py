"""Archived cards and inspect choices use synthetic, observed dashboard markup."""

from __future__ import annotations

import html
import json
import unittest

from _campaign_fixtures import campaign_page, list_snapshot, request_order, sent_component
from _fixtures import UPDATE_URI, component_html, page_html, snapshot_json, update_body
from _support import LocalServer, Reply

from outcraft_cli.client import Client
from outcraft_cli.html import parse_page
from outcraft_cli.transport import Transport
from outcraft_cli.views import campaigns_from_page, section_path

ARCHIVED = {"id": 7, "name": "Synthetic", "status": "Archived"}


def archived_card(extra: str = "") -> str:
    return (
        '<div wire:key="componentId.table.records.7">'
        '<div class="fi-size-sm fi-font-semibold fi-ta-text-item">Synthetic</div>'
        '<div class="fi-ta-text-item">Archived 33 seconds ago</div>'
        "<button wire:click=\"mountAction('unarchive', {}, "
        "JSON.parse('{\\u0022recordKey\\u0022:\\u00227\\u0022,"
        "\\u0022table\\u0022:true}'))\">Re-Activate</button>"
        f"{extra}</div>"
    )


def archived_page(extra: str = "") -> str:
    component = component_html(list_snapshot()).replace("</div>", archived_card(extra) + "</div>")
    return page_html(component)


def fields_page(markup: str, data: dict[str, object] | None = None) -> str:
    component = component_html(snapshot_json(data=data or {})).replace("</div>", markup + "</div>")
    return page_html(component)


class ArchivedShapeTest(unittest.TestCase):
    def test_archived_record_without_link_uses_key_name_and_archived_status(self) -> None:
        page = parse_page(archived_page())
        self.assertEqual(page.root.find("a"), [])
        self.assertEqual(page.component(key="tableSearch").data["tableSearch"], "")
        self.assertEqual(campaigns_from_page(page), [ARCHIVED])

    def test_nested_action_and_table_markup_are_not_additional_records(self) -> None:
        nested = (
            '<div wire:key="componentId.table.records.7.actions">'
            '<table><tr><td class="fi-ta-text-item">Not a campaign name</td>'
            "<td><button wire:click=\"mountAction('edit', {}, "
            '{&quot;recordKey&quot;:&quot;99&quot;,&quot;table&quot;:true})">'
            "Not a campaign</button></td></tr></table></div>"
        )
        self.assertEqual(campaigns_from_page(parse_page(archived_page(nested))), [ARCHIVED])

    def test_active_href_only_cards_keep_their_name_and_badge(self) -> None:
        page = parse_page(campaign_page(((7, "Synthetic", "Paused"),)))
        self.assertEqual(
            campaigns_from_page(page), [{"id": 7, "name": "Synthetic", "status": "Paused"}]
        )

    def test_keyed_active_card_with_nested_links_is_returned_once(self) -> None:
        source = campaign_page(((7, "Synthetic", "Running"),))
        source = source.replace(
            '<a href="/campaigns/7/onboarding">',
            '<div wire:key="componentId.table.records.7"><a href="/campaigns/7/onboarding">',
        ).replace(
            "</a>",
            '</a><a href="/campaigns/7/onboarding">'
            '<li class="fi-ta-text-item">Synthetic</li>'
            '<span class="fi-badge">Running</span></a></div>',
            1,
        )
        self.assertEqual(
            campaigns_from_page(parse_page(source)),
            [{"id": 7, "name": "Synthetic", "status": "Running"}],
        )


class ArchiveLoopbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = self.enterContext(LocalServer())
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))

    def test_campaigns_archived_reads_the_linkless_dom(self) -> None:
        self.server.route("GET", "/campaigns/archived", Reply.html(archived_page()))
        self.assertEqual(self.client.campaigns(archived=True), [ARCHIVED])
        self.assertEqual(request_order(self.server), [("GET", "/campaigns/archived")])

    def test_archive_verifies_linkless_record_after_it_disappears_from_active(self) -> None:
        self.server.route(
            "GET",
            "/campaigns",
            Reply.html(campaign_page(((7, "Synthetic", "Paused"),))),
            Reply.html(campaign_page(())),
        )
        self.server.route("GET", "/campaigns/archived", Reply.html(archived_page()))
        mounted = list_snapshot(
            "archive-mounted",
            mountedActions=[[{"name": "archive", "data": {}}], {"s": "arr"}],
        )
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(update_body(mounted, {})),
            Reply.json(update_body(list_snapshot("archive-saved"), {})),
        )

        self.assertEqual(
            self.client.archive(7, confirm=True),
            {**ARCHIVED, "operation": "archive", "permanently_deleted": False},
        )
        self.assertEqual(
            request_order(self.server),
            [
                ("GET", "/campaigns"),
                ("POST", UPDATE_URI),
                ("POST", UPDATE_URI),
                ("GET", "/campaigns"),
                ("GET", "/campaigns/archived"),
            ],
        )
        first, second = [
            sent_component(request) for request in self.server.requests_to("POST", UPDATE_URI)
        ]
        self.assertEqual(first["calls"][0]["method"], "mountAction")
        self.assertEqual(
            first["calls"][0]["params"], ["archive", {}, {"recordKey": "7", "table": True}]
        )
        self.assertEqual(second["snapshot"], mounted)
        self.assertEqual(second["calls"][0]["method"], "callMountedAction")

    def test_source_section_uses_onboarding_entry_not_api_source(self) -> None:
        self.assertEqual(section_path(7, "source"), "/campaigns/7/onboarding")

    def test_manual_source_inspect_follows_onboarding_redirect_to_company(self) -> None:
        self.server.route(
            "GET",
            "/campaigns/7/onboarding",
            Reply.redirect("/campaigns/7/onboarding/company"),
        )
        self.server.route(
            "GET",
            "/campaigns/7/onboarding/company",
            Reply.html(fields_page('<input wire:model="data.company" type="text">')),
        )
        result = self.client.inspect(7, "source")
        self.assertEqual(result["url"], self.server.base_url + "/campaigns/7/onboarding/company")
        self.assertEqual(
            request_order(self.server),
            [("GET", "/campaigns/7/onboarding"), ("GET", "/campaigns/7/onboarding/company")],
        )

    def test_inspect_exposes_radio_values_and_matching_labels(self) -> None:
        markup = (
            '<label for="unrelated">Do not use this label</label>'
            '<label for="objective-replies"><span>Get replies</span></label>'
            '<input id="objective-replies" type="radio" wire:model.live="data.objective" '
            'value="replies">'
            '<input id="provider-synthetic" type="radio" wire:model="data.provider" '
            'value="synthetic">'
            '<label for="provider-synthetic">Synthetic &amp; partners</label>'
        )
        self.server.route("GET", "/choices", Reply.html(fields_page(markup)))
        result = self.client.inspect_path("/choices")
        self.assertEqual(
            result["components"][0]["fields"],
            [
                {
                    "path": "data.objective",
                    "type": "radio",
                    "id": "objective-replies",
                    "value": "replies",
                    "label": "Get replies",
                },
                {
                    "path": "data.provider",
                    "type": "radio",
                    "id": "provider-synthetic",
                    "value": "synthetic",
                    "label": "Synthetic & partners",
                },
            ],
        )


class SafeFieldsTest(unittest.TestCase):
    def test_fields_without_label_or_value_keep_the_existing_exact_shape(self) -> None:
        page = parse_page(
            fields_page(
                '<input wire:model="search" type="search">'
                '<input wire:model="data.name" type="text">'
                '<textarea wire:model="data.notes"></textarea>'
            )
        )
        self.assertEqual(
            page.components[0].fields,
            [
                {"path": "search", "type": "search"},
                {"path": "data.name", "type": "text"},
                {"path": "data.notes", "type": "textarea"},
            ],
        )

    def test_password_dom_and_snapshot_secrets_never_appear_in_fields(self) -> None:
        dom_secret = "synthetic-dom-password-not-a-secret"
        snapshot_secret = "synthetic-snapshot-password-not-a-secret"
        page = parse_page(
            fields_page(
                '<label for="password">Password</label>'
                f'<input id="password" type="password" wire:model="password" '
                f'value="{html.escape(dom_secret, quote=True)}">'
                '<input type="text" wire:model="data.api_key">',
                {"password": snapshot_secret, "data": {"api_key": snapshot_secret}},
            )
        )
        fields = page.components[0].fields
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[0]["path"], "password")
        self.assertEqual(fields[0]["type"], "password")
        self.assertTrue(all("value" not in field for field in fields))
        self.assertNotIn(dom_secret, json.dumps(fields))
        self.assertNotIn(snapshot_secret, json.dumps(fields))


if __name__ == "__main__":
    unittest.main()
