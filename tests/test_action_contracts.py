"""Archive, duplicate, pause/resume and generic actions follow the mounted-action contract."""

from __future__ import annotations

import html
import io
import json
import os
import tempfile
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from _campaign_fixtures import campaign_page, list_snapshot, sent_component
from _fixtures import UPDATE_URI, page_html, snapshot_json, update_body
from _support import LocalServer, Reply

from outcraft_cli.actions import find_action_component, mounted_action
from outcraft_cli.cli import main
from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.html import parse_page
from outcraft_cli.transport import Transport

ACTIVE = (7, "Synthetic campaign", "Paused")
SEQUENCE_PATH = "/campaigns/7/onboarding/campaign/sequence"
STEPS_NAME = "synthetic.sequence-steps"
EDIT_ROW_3 = ["edit", {}, {"recordKey": "3", "table": True}]
Records = tuple[tuple[int, str, str], ...]


def mounted(checksum: str, *names: str, snapshot: Callable[..., str] = list_snapshot) -> str:
    actions = [{"name": name, "data": {}} for name in names]
    return snapshot(checksum, mountedActions=[actions, {"s": "arr"}])


def steps_snapshot(checksum: str = "steps-original", **extra: object) -> str:
    data = {"mountedActions": [[], {"s": "arr"}], **extra}
    return snapshot_json(name=STEPS_NAME, data=data, checksum=checksum, component_id="steps")


def action_markup(snapshot: str, identifier: str, expression: str, label: str) -> str:
    return (
        f'<div wire:id="{identifier}" wire:snapshot="{html.escape(snapshot, quote=True)}">'
        f'<button wire:click="{html.escape(expression, quote=True)}">{label}</button></div>'
    )


def sequence_page() -> str:
    edit = 'mountAction(\'edit\', {}, {"recordKey": "3", "table": true})'
    other = snapshot_json(name="synthetic.topbar", data={"open": False}, component_id="top")
    return page_html(
        action_markup(other, "top", "mountAction('editor')", "Editor"),
        action_markup(steps_snapshot(), "steps", edit, "Edit step"),
    )


class WriteAwareServer(LocalServer):
    """Pages are (before, after); after serves once mount and submit POSTs arrived, however
    many reads the client makes in between."""

    def __init__(self) -> None:
        super().__init__()
        self.pages: dict[str, tuple[str, str]] = {}

    def _next_reply(self, method: str, path: str) -> Reply:
        route = urlsplit(path).path
        if method == "GET" and route in self.pages:
            saved = len(self.requests_to("POST", UPDATE_URI)) >= 2
            return Reply.html(self.pages[route][saved])
        return super()._next_reply(method, path)


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = self.enterContext(WriteAwareServer())
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))

    def posts(self) -> list[dict]:
        return [sent_component(post) for post in self.server.requests_to("POST", UPDATE_URI)]


class ClientActionContractsTest(ServerCase):
    def setUp(self) -> None:
        super().setUp()
        self.lists(active=(ACTIVE,))

    def lists(
        self, *, active: Records, after: Records | None = None, archived: Records = ()
    ) -> None:
        saved = active if after is None else after
        self.server.pages["/campaigns"] = (campaign_page(active), campaign_page(saved))
        self.server.pages["/campaigns/archived"] = (campaign_page(()), campaign_page(archived))

    def replies(self, action: str, effects: dict | None = None, remaining: bool = False) -> str:
        mount = mounted(f"{action}-mounted", action)
        saved = mounted(f"{action}-left", action) if remaining else list_snapshot(f"{action}-saved")
        bodies = (update_body(mount), update_body(saved, effects or {}))
        self.server.route("POST", UPDATE_URI, *map(Reply.json, bodies))
        return mount

    def assert_mount_then_submit(self, action: str, mount: str) -> None:
        first, second = self.posts()
        self.assertEqual(first["snapshot"], list_snapshot())
        self.assertEqual(first["calls"][0]["method"], "mountAction")
        self.assertEqual(
            first["calls"][0]["params"], [action, {}, {"recordKey": "7", "table": True}]
        )
        self.assertEqual(second["snapshot"], mount, "submit must use the fresh mounted snapshot")
        self.assertEqual(second["calls"][0]["method"], "callMountedAction")
        self.assertEqual(second["updates"], {})

    def test_archive_submits_then_finds_record_in_archived_list(self) -> None:
        self.lists(active=(ACTIVE,), after=(), archived=((7, ACTIVE[1], "Archived"),))
        mount = self.replies("archive")
        result = self.client.archive(7, confirm=True)
        self.assert_mount_then_submit("archive", mount)
        self.assertEqual((result["id"], result["status"]), (7, "Archived"))
        self.assertEqual(result["operation"], "archive")
        self.assertIs(result["permanently_deleted"], False)

    def test_archive_still_active_or_not_archived_is_not_verified(self) -> None:
        for after, archived in (((ACTIVE,), ((7, ACTIVE[1], "Archived"),)), ((), ())):
            with self.subTest(after=after, archived=archived):
                self.server.requests.clear()
                self.lists(active=(ACTIVE,), after=after, archived=archived)
                self.replies("archive")
                with self.assertRaises(OutcraftError):
                    self.client.archive(7, confirm=True)
                self.assertEqual(len(self.posts()), 2)

    def test_duplicate_verifies_redirect_target_in_the_list(self) -> None:
        self.lists(active=(ACTIVE,), after=(ACTIVE, (8, "Synthetic campaign copy", "Draft")))
        mount = self.replies("duplicate", {"redirect": "/campaigns/8/onboarding"})
        result = self.client.duplicate(7, confirm=True)
        self.assert_mount_then_submit("duplicate", mount)
        self.assertEqual((result["id"], result["name"]), (8, "Synthetic campaign copy"))

    def test_duplicate_without_a_verifiable_redirect_fails_once(self) -> None:
        for effects in ({}, {"redirect": "/campaigns/9/onboarding"}):
            with self.subTest(effects=effects):
                self.server.requests.clear()
                self.lists(active=(ACTIVE,), after=(ACTIVE, (8, "Copy", "Draft")))
                self.replies("duplicate", effects)
                with self.assertRaises(OutcraftError):
                    self.client.duplicate(7, confirm=True)
                self.assertEqual(len(self.posts()), 2)

    def test_resume_and_pause_toggle_with_table_record_then_verify(self) -> None:
        for running, before, desired in ((True, "Paused", "Running"), (False, "Running", "Paused")):
            with self.subTest(running=running):
                self.server.requests.clear()
                self.lists(active=((7, ACTIVE[1], before),), after=((7, ACTIVE[1], desired),))
                mount = self.replies("toggle_campaign")
                result = self.client.set_running(7, running, confirm=True)
                self.assert_mount_then_submit("toggle_campaign", mount)
                self.assertEqual(result["status"], desired)

    def test_resume_or_pause_in_the_desired_state_never_posts(self) -> None:
        for running, status in ((True, "Running"), (False, "Paused")):
            with self.subTest(running=running):
                self.server.requests.clear()
                self.lists(active=((7, ACTIVE[1], status),))
                result = self.client.set_running(7, running, confirm=True)
                self.assertEqual(result["status"], status)
                self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])

    def test_toggle_that_does_not_reach_the_state_fails_without_toggling_back(self) -> None:
        self.lists(active=(ACTIVE,), after=(ACTIVE,))
        self.replies("toggle_campaign")
        with self.assertRaises(OutcraftError):
            self.client.set_running(7, True, confirm=True)
        self.assertEqual(len(self.posts()), 2)

    def test_absent_record_or_unknown_status_never_posts(self) -> None:
        for records, operation in (
            (((12, "Other", "Paused"),), lambda: self.client.archive(7, confirm=True)),
            (((12, "Other", "Paused"),), lambda: self.client.duplicate(7, confirm=True)),
            (((12, "Other", "Paused"),), lambda: self.client.set_running(7, True, confirm=True)),
            (((7, ACTIVE[1], "Draft"),), lambda: self.client.set_running(7, True, confirm=True)),
        ):
            with self.subTest(records=records), self.assertRaises(OutcraftError):
                self.lists(active=records)
                operation()
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])

    def test_action_still_mounted_after_submit_is_refused(self) -> None:
        cases = (
            ("archive", lambda: self.client.archive(7, confirm=True)),
            ("duplicate", lambda: self.client.duplicate(7, confirm=True)),
            ("toggle_campaign", lambda: self.client.set_running(7, True, confirm=True)),
        )
        for action, operation in cases:
            with self.subTest(action=action):
                self.server.requests.clear()
                self.replies(action, remaining=True)
                with self.assertRaises(OutcraftError):
                    operation()
                self.assertEqual(len(self.posts()), 2)

    def test_unconfirmed_mutations_fail_before_any_request(self) -> None:
        operations = (
            lambda: self.client.archive(7),
            lambda: self.client.duplicate(7),
            lambda: self.client.set_running(7, True),
            lambda: self.client.set_running(7, False),
            lambda: self.client.action(7, "sequence", "edit", record="3"),
        )
        for index, operation in enumerate(operations):
            with self.subTest(index=index), self.assertRaises(OutcraftError):
                operation()
        self.assertEqual(self.server.requests, [])


class GenericActionContractsTest(ServerCase):
    def setUp(self) -> None:
        super().setUp()
        self.server.route("GET", "/campaigns", Reply.html(campaign_page()))
        self.server.route("GET", SEQUENCE_PATH, Reply.html(sequence_page()))

    def replies(self, *snapshots: str) -> None:
        self.server.route("POST", UPDATE_URI, *(Reply.json(update_body(s, {})) for s in snapshots))

    def run_action(self, action: str, **options: object) -> None:
        page = self.client.livewire.page(SEQUENCE_PATH)
        mounted_action(self.client.livewire, page, page.component(STEPS_NAME), action, **options)

    def test_find_action_component_selects_only_the_owner(self) -> None:
        page = parse_page(sequence_page())
        self.assertEqual(find_action_component(page, "edit").name, STEPS_NAME)
        self.assertEqual(find_action_component(page, "edit", STEPS_NAME).name, STEPS_NAME)
        self.assertEqual(find_action_component(page, "editor").name, "synthetic.topbar")
        for action, name in (("delete", None), ("edit", "synthetic.topbar")):
            with self.subTest(action=action, name=name), self.assertRaises(OutcraftError):
                find_action_component(page, action, name)

    def test_record_context_and_data_reach_the_last_mounted_form(self) -> None:
        nested = mounted("nested", "edit", "confirm", snapshot=steps_snapshot)
        self.replies(nested, steps_snapshot("saved"))
        self.run_action("edit", data={"subject": "Hi"}, record="3")
        first, second = self.posts()
        self.assertEqual(first["calls"][0]["params"], EDIT_ROW_3)
        self.assertEqual(second["snapshot"], nested)
        self.assertEqual(second["calls"][0]["method"], "callMountedAction")
        self.assertEqual(second["updates"], {"mountedActions.1.data.subject": "Hi"})

    def test_inspect_and_formless_actions_post_once(self) -> None:
        for label, reply, options in (
            ("inspect", mounted("form", "edit", snapshot=steps_snapshot), {"inspect": True}),
            ("formless", steps_snapshot("ran"), {}),
        ):
            with self.subTest(label=label):
                self.server.requests.clear()
                self.replies(reply)
                self.run_action("edit", record="3", **options)
                self.assertEqual(len(self.posts()), 1)

    def test_lost_data_or_remaining_form_is_refused(self) -> None:
        form = mounted("form", "edit", snapshot=steps_snapshot)
        for label, replies in (("formless", (steps_snapshot("ran"),)), ("remaining", (form, form))):
            with self.subTest(label=label), self.assertRaises(OutcraftError):
                self.replies(*replies)
                self.run_action("edit", data={"subject": "Hi"}, record="3")

    def test_client_action_submits_with_fresh_snapshot_and_stays_unverified(self) -> None:
        mount = mounted("mounted", "edit", snapshot=steps_snapshot)
        self.replies(mount, steps_snapshot("saved"))
        result = self.client.action(7, "sequence", "edit", record="3", data={"a": 1}, confirm=True)
        self.assertEqual((result["submitted"], result["verified"]), (True, False))
        first, second = self.posts()
        self.assertEqual(first["calls"][0]["params"], EDIT_ROW_3)
        self.assertEqual(second["snapshot"], mount)

    def test_cli_sequence_show_mounts_edit_over_http_without_submitting(self) -> None:
        self.replies(mounted("mounted", "edit", snapshot=steps_snapshot))
        session = str(Path(self.enterContext(tempfile.TemporaryDirectory())) / "session")
        argv = ["--base-url", self.server.base_url, "--session-file", session]
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main(argv + ["sequence", "show", "7", "3", "--yes"])
        self.assertEqual((code, stderr.getvalue()), (0, ""))
        payload = json.loads(stdout.getvalue())
        self.assertEqual((payload["submitted"], payload["verified"]), (False, False))
        (only,) = self.posts()
        self.assertEqual(only["calls"][0]["params"], EDIT_ROW_3)
        self.assertTrue(self.server.requests_to("GET", SEQUENCE_PATH))


if __name__ == "__main__":
    unittest.main()
