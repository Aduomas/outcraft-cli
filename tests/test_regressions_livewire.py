"""Nested ownership and partial rendering retain the current component contract."""

from __future__ import annotations

import copy
import html
import unittest

from _fixtures import UPDATE_URI, page_html, snapshot_json, update_body
from _support import LocalServer, Reply

from outcraft_cli.actions import find_action_component
from outcraft_cli.html import parse_page
from outcraft_cli.livewire import Livewire
from outcraft_cli.transport import Transport


def markup(snapshot: str, identifier: str, body: str) -> str:
    escaped = html.escape(snapshot, quote=True)
    return f'<div wire:id="{identifier}" wire:snapshot="{escaped}">{body}</div>'


class NestedComponentRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        child = markup(
            snapshot_json(
                name="synthetic.child", component_id="child", data={"data": {"name": "Child"}}
            ),
            "child",
            "<button wire:click=\"mountAction('edit')\">Edit child</button>"
            '<input wire:model.live="data.name" type="text">',
        )
        parent = markup(
            snapshot_json(name="synthetic.parent", component_id="parent", data={"search": ""}),
            "parent",
            '<button wire:click="refresh">Refresh parent</button>'
            '<input wire:model="search" type="search">' + child,
        )
        self.page = parse_page(page_html(parent))

    def test_parent_actions_stop_at_child_component_boundary(self) -> None:
        self.assertEqual(
            [component.name for component in self.page.components],
            ["synthetic.parent", "synthetic.child"],
        )
        parent, child = self.page.components
        self.assertEqual(parent.actions, [{"label": "Refresh parent", "expression": "refresh"}])
        self.assertEqual(
            child.actions, [{"label": "Edit child", "expression": "mountAction('edit')"}]
        )

    def test_parent_fields_stop_at_child_component_boundary(self) -> None:
        parent, child = self.page.components
        self.assertEqual(parent.fields, [{"path": "search", "type": "search"}])
        self.assertEqual(child.fields, [{"path": "data.name", "type": "text"}])

    def test_action_resolution_selects_only_the_component_owning_the_action(self) -> None:
        child = find_action_component(self.page, "edit")
        self.assertIs(child, self.page.components[1])

    def test_snapshotless_nested_component_is_still_an_ownership_boundary(self) -> None:
        source = markup(
            snapshot_json(name="synthetic.parent", component_id="parent", data={}),
            "parent",
            '<div wire:id="lazy-child"><button wire:click="mountAction(\'edit\')">Edit</button>'
            '<input wire:model="data.private"></div>',
        )
        parent = parse_page(page_html(source)).components[0]
        self.assertEqual(parent.actions, [])
        self.assertEqual(parent.fields, [])


class PartialRenderingRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.original_snapshot = snapshot_json(data={"search": ""}, checksum="original")
        self.fresh_snapshot = snapshot_json(data={"search": "fresh"}, checksum="fresh-partial")
        body = (
            "<table><tr><td>kept</td></tr></table>"
            "<button wire:click=\"mountAction('edit')\">Edit</button>"
            '<div wire:partial="action-modals">old</div>'
        )
        self.server.route(
            "GET",
            "/campaigns",
            Reply.html(page_html(markup(self.original_snapshot, "lw-abc123", body))),
        )
        self.livewire = Livewire(Transport(base_url=self.server.base_url, session_file=None))

    def test_partial_only_update_preserves_rows_actions_and_original_component(self) -> None:
        replacement = (
            '<div wire:partial="action-modals"><input wire:model="data.name">new form</div>'
        )
        effects = {"partials": {"action-modals": replacement}}
        self.server.route("POST", UPDATE_URI, Reply.json(update_body(self.fresh_snapshot, effects)))
        page = self.livewire.page("/campaigns")
        original = page.components[0]
        original_root = copy.deepcopy(original.root)

        result = self.livewire.call(page, original, "mountAction", params=["edit"])

        self.assertEqual([cell.text for cell in result.root.find("td")], ["kept"])
        self.assertEqual(result.actions, [{"label": "Edit", "expression": "mountAction('edit')"}])
        modals = [
            node for node in result.root.find() if node.attrs.get("wire:partial") == "action-modals"
        ]
        self.assertEqual(len(modals), 1)
        self.assertEqual(modals[0].text, "new form")
        self.assertEqual(result.fields, [{"path": "data.name", "type": "input"}])
        self.assertEqual(result.snapshot, self.fresh_snapshot)
        self.assertEqual(result.data, {"search": "fresh"})
        self.assertEqual(result.effects, effects)
        self.assertEqual(
            original.root,
            original_root,
            "Partial rendering must not mutate the caller's component",
        )
        self.assertEqual(original.snapshot, self.original_snapshot)

    def test_full_html_update_replaces_old_rows_instead_of_merging_them(self) -> None:
        replacement = '<div><button wire:click="refresh">Replacement</button></div>'
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(update_body(self.fresh_snapshot, {"html": replacement})),
        )
        page = self.livewire.page("/campaigns")
        original = page.components[0]
        original_root = copy.deepcopy(original.root)

        result = self.livewire.call(page, original, "refresh")

        self.assertEqual(result.root.find("table"), [])
        self.assertEqual(result.actions, [{"label": "Replacement", "expression": "refresh"}])
        self.assertNotIn("old", result.root.text)
        self.assertEqual(result.snapshot, self.fresh_snapshot)
        self.assertEqual(original.root, original_root)


if __name__ == "__main__":
    unittest.main()
