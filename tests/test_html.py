"""Contract: parse_page and unwrap turn Livewire HTML into exact snapshots and plain data."""

from __future__ import annotations

import copy
import html
import json
import unittest

from _fixtures import (
    CAMPAIGNS_DATA,
    CAMPAIGNS_NAME,
    CAMPAIGNS_UNWRAPPED,
    CSRF,
    UPDATE_URI,
    campaigns_page,
    component_html,
    page_html,
    snapshot_json,
)

from outcraft_cli.errors import OutcraftError
from outcraft_cli.html import parse_page, unwrap


class ParsePageTest(unittest.TestCase):
    def test_reads_csrf_and_update_uri_from_livewire_script(self) -> None:
        page = parse_page(campaigns_page())
        self.assertEqual(page.csrf, CSRF)
        self.assertEqual(page.update_uri, UPDATE_URI)

    def test_falls_back_to_meta_csrf_token(self) -> None:
        page = parse_page(campaigns_page(csrf_in="meta"))
        self.assertEqual(page.csrf, CSRF)

    def test_meta_and_script_csrf_agree(self) -> None:
        page = parse_page(campaigns_page(csrf_in="both"))
        self.assertEqual(page.csrf, CSRF)

    def test_snapshot_is_the_exact_unescaped_attribute_string(self) -> None:
        original = snapshot_json()
        self.assertIn("\\/", original, "fixture must carry PHP escaped slashes")
        self.assertIn("\\u00e9", original, "fixture must carry unicode escapes")

        page = parse_page(page_html(component_html(original)))

        self.assertEqual(len(page.components), 1)
        snapshot = page.components[0].snapshot
        self.assertIsInstance(snapshot, str)
        self.assertEqual(snapshot, original, "snapshot must not be re-serialized")
        self.assertNotEqual(snapshot, json.dumps(json.loads(original)))

    def test_component_name_and_unwrapped_data(self) -> None:
        component = parse_page(campaigns_page()).components[0]
        self.assertEqual(component.name, CAMPAIGNS_NAME)
        self.assertEqual(component.data, CAMPAIGNS_UNWRAPPED)

    def test_effects_are_parsed_and_default_to_empty(self) -> None:
        with_effects = component_html(
            snapshot_json(), effects={"listeners": ["refresh"], "url": []}
        )
        without = component_html(
            snapshot_json(name="nav.menu", component_id="lw-nav"), component_id="lw-nav"
        )
        page = parse_page(page_html(with_effects, without))

        self.assertEqual(page.components[0].effects, {"listeners": ["refresh"], "url": []})
        self.assertEqual(page.components[1].effects, {})

    def test_multiple_components_keep_document_order(self) -> None:
        first = component_html(snapshot_json(name="nav.menu", component_id="a"), component_id="a")
        second = component_html(snapshot_json(component_id="b"), component_id="b")
        page = parse_page(page_html(first, second))
        self.assertEqual([c.name for c in page.components], ["nav.menu", CAMPAIGNS_NAME])

    def test_elements_without_snapshot_are_ignored(self) -> None:
        placeholder = component_html(None, component_id="lazy-child")
        page = parse_page(page_html(placeholder, component_html(snapshot_json())))
        self.assertEqual([c.name for c in page.components], [CAMPAIGNS_NAME])

    def test_page_without_components_is_not_an_error(self) -> None:
        page = parse_page(page_html())
        self.assertEqual(list(page.components), [])
        self.assertEqual(page.csrf, CSRF)

    def test_malformed_snapshot_raises(self) -> None:
        broken = '<div wire:id="x" wire:snapshot="{&quot;data&quot;:{"></div>'
        with self.assertRaises(OutcraftError):
            parse_page(page_html(broken))

    def test_snapshot_that_is_not_an_object_raises(self) -> None:
        for raw in ("[]", '"text"', "42"):
            with self.subTest(raw=raw), self.assertRaises(OutcraftError):
                parse_page(page_html(f'<div wire:id="x" wire:snapshot="{html.escape(raw)}"></div>'))

    def test_markup_inside_snapshot_does_not_confuse_the_parser(self) -> None:
        data = {
            "note": '<script data-csrf="EVIL" data-update-uri="https://evil.example/u"></script>'
        }
        page = parse_page(page_html(component_html(snapshot_json(data=data))))
        self.assertEqual(page.csrf, CSRF)
        self.assertEqual(page.update_uri, UPDATE_URI)
        self.assertEqual(page.components[0].data, data)


class UnwrapTest(unittest.TestCase):
    def test_unwraps_livewire_tuple(self) -> None:
        self.assertEqual(unwrap([[1, 2, 3], {"s": "arr"}]), [1, 2, 3])

    def test_unwraps_tuple_with_extra_metadata_keys(self) -> None:
        self.assertEqual(
            unwrap(["2026-10-19", {"s": "cbn", "class": "Carbon\\Carbon"}]), "2026-10-19"
        )
        self.assertEqual(
            unwrap([{"a": 1}, {"s": "clctn", "class": "Illuminate\\Support\\Collection"}]), {"a": 1}
        )

    def test_unwraps_null_valued_tuple(self) -> None:
        self.assertIsNone(unwrap([None, {"s": "enm", "class": "App\\Status"}]))

    def test_unwraps_recursively_through_dicts_and_lists(self) -> None:
        self.assertEqual(unwrap(CAMPAIGNS_DATA), CAMPAIGNS_UNWRAPPED)

    def test_unwraps_nested_tuples(self) -> None:
        nested = [[[[1], {"s": "arr"}], [[2], {"s": "arr"}]], {"s": "arr"}]
        self.assertEqual(unwrap(nested), [[1], [2]])

    def test_preserves_ordinary_two_element_lists(self) -> None:
        for value in (
            ["active", "paused"],
            [1, 2],
            ["x", {"y": 1}],
            [1, {}],
            [{"s": "arr"}, "tail"],
            [[1], {"S": "arr"}],
            [[1], {"s": 5}],
        ):
            with self.subTest(value=value):
                self.assertEqual(unwrap(value), value)

    def test_preserves_other_list_lengths(self) -> None:
        self.assertEqual(unwrap([]), [])
        self.assertEqual(unwrap([[1], {"s": "arr"}, "extra"]), [[1], {"s": "arr"}, "extra"])

    def test_unwraps_tuples_inside_ordinary_lists(self) -> None:
        self.assertEqual(unwrap([[[1], {"s": "arr"}], "x", 3]), [[1], "x", 3])

    def test_preserves_dicts_that_look_like_metadata(self) -> None:
        self.assertEqual(unwrap({"s": "arr"}), {"s": "arr"})

    def test_scalars_pass_through(self) -> None:
        for value in ("text", 0, 1.5, True, None):
            with self.subTest(value=value):
                self.assertEqual(unwrap(value), value)

    def test_does_not_mutate_input(self) -> None:
        before = copy.deepcopy(CAMPAIGNS_DATA)
        unwrap(CAMPAIGNS_DATA)
        self.assertEqual(CAMPAIGNS_DATA, before)


if __name__ == "__main__":
    unittest.main()
