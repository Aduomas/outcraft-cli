"""Section updates dispatch only the requested fields and verify their persisted values."""

from __future__ import annotations

import unittest

from _campaign_fixtures import request_order, sent_component
from _fixtures import UPDATE_URI, component_html, page_html, snapshot_json, update_body
from _support import LocalServer, Reply

from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport

SECTION_PATH = "/campaigns/7/onboarding/campaign/general"
FORM_NAME = "synthetic.general-form"
FORM_DATA = {
    "should_shorten_links": True,
    "campaign_name": "Keep this name",
    "unrelated_setting": "leave unchanged",
}


def form_snapshot(
    shorten: bool = True, checksum: str = "form-original", component_id: str = "form"
) -> str:
    return snapshot_json(
        name=FORM_NAME,
        data={"data": {**FORM_DATA, "should_shorten_links": shorten}},
        checksum=checksum,
        component_id=component_id,
    )


def section_page(shorten: bool = True, duplicate: bool = False) -> str:
    decoy = component_html(
        snapshot_json(
            name="synthetic.help",
            data={"data": {"should_shorten_links": True}},
            component_id="help",
        ),
        effects={"listeners": ["refresh"]},
        component_id="help",
    )
    form = component_html(
        form_snapshot(shorten), effects={"listeners": ["move-to-next-step"]}, component_id="form"
    )
    extra = (
        component_html(
            form_snapshot(component_id="second-form"),
            effects={"listeners": ["move-to-next-step"]},
            component_id="second-form",
        )
        if duplicate
        else ""
    )
    return page_html(decoy, form, extra)


class ClientUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))
        self.server.route(
            "GET", SECTION_PATH, Reply.html(section_page()), Reply.html(section_page(False))
        )
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(
                update_body(
                    form_snapshot(False, checksum="form-saved"),
                    {"dispatches": []},
                )
            ),
        )

    def test_general_update_selects_listener_form_sends_patch_and_verifies(self) -> None:
        patch = {"should_shorten_links": False}
        result = self.client.update(7, "general", patch, confirm=True)

        self.assertIsInstance(result, dict)
        self.assertEqual(patch, {"should_shorten_links": False})
        self.assertEqual(
            request_order(self.server),
            [
                ("GET", SECTION_PATH),
                ("POST", UPDATE_URI),
                ("GET", SECTION_PATH),
            ],
        )
        posts = self.server.requests_to("POST", UPDATE_URI)
        self.assertEqual(len(posts), 1)
        sent = sent_component(posts[0])
        self.assertEqual(sent["snapshot"], form_snapshot())
        self.assertEqual(sent["updates"], {"data.should_shorten_links": False})
        self.assertEqual(sent["calls"][0]["method"], "__dispatch")
        self.assertEqual(sent["calls"][0]["params"], ["move-to-next-step", {}])

    def test_update_fails_when_reloaded_section_does_not_match_patch(self) -> None:
        self.server.route("GET", SECTION_PATH, Reply.html(section_page()))
        with self.assertRaises(OutcraftError):
            self.client.update(7, "general", {"should_shorten_links": False}, confirm=True)
        self.assertEqual(len(self.server.requests_to("GET", SECTION_PATH)), 2)

    def test_unknown_field_prevents_the_entire_patch_from_being_posted(self) -> None:
        with self.assertRaises(OutcraftError):
            self.client.update(
                7,
                "general",
                {
                    "should_shorten_links": False,
                    "not_a_campaign_field": "unexpected",
                },
                confirm=True,
            )
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])

    def test_unknown_section_fails_before_any_request(self) -> None:
        with self.assertRaises(OutcraftError):
            self.client.update(7, "unknown-section", {"should_shorten_links": False}, confirm=True)
        self.assertEqual(self.server.requests, [])

    def test_missing_confirmation_fails_before_any_request(self) -> None:
        with self.assertRaises(OutcraftError):
            self.client.update(7, "general", {"should_shorten_links": False})
        self.assertEqual(self.server.requests, [])

    def test_two_matching_forms_fail_before_post(self) -> None:
        self.server.route("GET", SECTION_PATH, Reply.html(section_page(duplicate=True)))
        with self.assertRaises(OutcraftError):
            self.client.update(7, "general", {"should_shorten_links": False}, confirm=True)
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])

    def test_data_without_dispatch_listener_is_not_an_editable_form(self) -> None:
        self.server.route(
            "GET",
            SECTION_PATH,
            Reply.html(
                page_html(
                    component_html(
                        form_snapshot(),
                        effects={"listeners": ["refresh"]},
                        component_id="form",
                    )
                )
            ),
        )
        with self.assertRaises(OutcraftError):
            self.client.update(7, "general", {"should_shorten_links": False}, confirm=True)
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])


if __name__ == "__main__":
    unittest.main()
