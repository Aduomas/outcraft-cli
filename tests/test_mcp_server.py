"""The MCP server, driven over real stdio, against a scripted loopback dashboard."""

from __future__ import annotations

import re
import tempfile
import unittest
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import anyio
from _campaign_fixtures import campaign_page, list_snapshot
from _fixtures import UPDATE_URI, component_html, page_html, snapshot_json, update_body
from _mcp_support import (
    COOKIE,
    NETWORK,
    SECRETS,
    SPAWNS,
    Concurrency,
    ScriptedServer,
    Session,
    mcp_session,
    payload,
    texts,
    write_env,
)
from _support import Reply

READS = {
    "campaign_sections": set(),
    "list_campaigns": {"archived", "page", "search"},
    "get_campaign": {"campaign_id", "section"},
    "export_campaign": {"campaign_id"},
    "inspect_dashboard": {"path"},
    "list_leads": {"campaign_id", "status", "page", "per_page"},
    "count_leads": {"campaign_id"},
    "get_lead": {"lead_id"},
    "get_analytics": {"campaign_id", "range"},
}
WRITES = {
    "create_campaign": {"name": "Valid name"},
    "rename_campaign": {"campaign_id": 7, "name": "Valid name"},
    "update_campaign": {"campaign_id": 7, "section": "instructions", "changes": {"prompt": "x"}},
    "set_campaign_running": {"campaign_id": 7, "running": True},
    "archive_campaign": {"campaign_id": 7},
    "duplicate_campaign": {"campaign_id": 7},
    "publish_campaign": {"campaign_id": 7},
    "campaign_action": {"campaign_id": 7, "section": "sequence", "action": "edit", "inspect": True},
}
ACTION_PARAMS = {"record", "data", "arguments", "context", "component_name", "inspect"}
SECRET_NAME = re.compile(r"password|passwd|token|secret|cookie|authorization|csrf|api_?key|email")
FORM = {
    "prompt": "Be concise",
    "webhook": "https://hooks.example/api/v1/trigger/TRIGGER-SECRET-77",
    "api_key": "sk-API-SECRET-55",
}
RECORDS = ((7, "Synthetic Café & partners", "Paused"), (12, "Second campaign", "Draft"))
SET_COOKIE = [f"laravel_session={COOKIE}; Path=/; HttpOnly"]
TIMEOUT = 60


def dashboard(records: tuple[tuple[int, str, str], ...] = RECORDS) -> Reply:
    return Reply.html(campaign_page(records), cookies=SET_COOKIE)


def mounted_replies() -> tuple[Reply, ...]:
    mounted = list_snapshot(
        "mounted-fresh",
        mountedActions=[[{"name": "mounted", "data": {"name": "Old"}}], {"s": "arr"}],
    )
    return Reply.json(update_body(mounted)), Reply.json(
        update_body(list_snapshot("saved-fresh"), {"redirect": "/campaigns/8/onboarding"})
    )


class McpServerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.server = ScriptedServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)

    def session(self, *, valid_env: bool = True) -> AbstractAsyncContextManager[Session]:
        env_file = write_env(self.directory, valid=valid_env)
        return mcp_session(self.server.base_url, self.directory, env_file)

    def assert_contained(self, session: Session, *, network: bool) -> None:
        events = session.events()
        self.assertEqual([e for e in events if e[0] in SPAWNS], [], "no browser or subprocess")
        self.assertEqual([e for e in events if e[0] == "socket.bind"], [], "stdio only, no port")
        traffic = [e for e in events if e[0] in NETWORK]
        if not network:
            self.assertEqual(traffic, [])
            self.assertEqual(self.server.requests, [])
        for event, detail in traffic:
            self.assertIn("127.0.0.1", detail, f"{event} left loopback")
        exposed = session.exposed()
        for secret in (*SECRETS, "TRIGGER-SECRET-77", "sk-API-SECRET-55", "SECRET-BODY"):
            self.assertNotIn(secret, exposed)
        self.assertNotIn("Traceback", exposed)

    async def test_discovery_lists_contract_tools_without_http_or_browser(self) -> None:
        with anyio.fail_after(TIMEOUT):
            async with self.session() as session:
                self.assertIsNotNone(session.client.server_capabilities.tools)
                tools = {t.name: t for t in (await session.client.list_tools()).tools}
                self.assert_contained(session, network=False)
        self.assertEqual(set(tools), set(READS) | set(WRITES))
        for name, tool in tools.items():
            with self.subTest(tool=name):
                properties = set(tool.input_schema.get("properties", {}))
                hints = tool.annotations
                self.assertIsNotNone(hints)
                self.assertEqual([p for p in properties if SECRET_NAME.search(p)], [])
                if name in READS:
                    self.assertEqual(properties, READS[name])
                    self.assertIs(hints.read_only_hint, True)
                    self.assertIs(hints.destructive_hint, False)
                else:
                    expected = set(WRITES[name]) | {"confirm"}
                    if name == "campaign_action":
                        expected |= ACTION_PARAMS
                    self.assertEqual(properties, expected)
                    self.assertIs(hints.read_only_hint, False)
                    self.assertNotIn("confirm", tool.input_schema.get("required", []))
                    self.assertIs(tool.input_schema["properties"]["confirm"].get("default"), False)
                if name != "campaign_sections":
                    self.assertIs(hints.open_world_hint, True)
        self.assertRegex(tools["archive_campaign"].description or "", r"(?i)\bnot\b[^.]*permanent")

    async def test_writes_without_confirmation_make_no_request_even_with_invalid_env(self) -> None:
        with anyio.fail_after(TIMEOUT):
            async with self.session(valid_env=False) as session:
                for name, arguments in WRITES.items():
                    for confirm in ({}, {"confirm": False}):
                        with self.subTest(tool=name, confirm=confirm):
                            result = await session.call(name, {**arguments, **confirm})
                            self.assertTrue(result.is_error, texts(result))
                            self.assertRegex(texts(result), r"(?i)confirm")
                sections = payload(await session.call("campaign_sections"))
                self.assert_contained(session, network=False)
        self.assertEqual(sections["instructions"], "campaign/context")

    async def test_reads_parse_the_loopback_dashboard_and_redact_secrets(self) -> None:
        self.server.route("GET", "/campaigns", dashboard())
        form = snapshot_json(name="synthetic.context-form", data={"data": FORM})
        form_page = page_html(component_html(form, effects={"listeners": ["move-to-next-step"]}))
        context = "/campaigns/7/onboarding/campaign/context"
        self.server.route("GET", context, Reply.html(form_page, cookies=SET_COOKIE))
        with anyio.fail_after(TIMEOUT):
            async with self.session() as session:
                listed = payload(await session.call("list_campaigns"))
                found = payload(await session.call("list_campaigns", {"search": "café"}))
                shown = await session.call("get_campaign", {"campaign_id": 7})
                page = payload(await session.call("inspect_dashboard", {"path": "/campaigns"}))
                bad_section = await session.call(
                    "get_campaign", {"campaign_id": 7, "section": "nope"}
                )
                foreign = await session.call(
                    "inspect_dashboard", {"path": "https://outcraft.example.net/campaigns"}
                )
                self.assert_contained(session, network=True)
        self.assertEqual([(r["id"], r["name"], r["status"]) for r in listed], list(RECORDS))
        self.assertEqual([r["id"] for r in found], [7])
        self.assertFalse(shown.is_error, texts(shown))
        self.assertIn("Be concise", texts(shown))
        self.assertIn("/campaigns/7/onboarding", [link["href"] for link in page["links"]])
        self.assertTrue(bad_section.is_error)
        self.assertTrue(foreign.is_error)
        self.assertEqual(self.server.requests_to("POST", UPDATE_URI), [])
        self.assertTrue(self.server.requests_to("GET", context))

    async def test_confirmed_rename_and_create_run_the_dashboard_flow(self) -> None:
        renamed = ((7, "Renamed Café & partners", "Paused"),)
        created = (*renamed, (8, "Created campaign", "Draft"))
        self.server.route_fn(
            "GET",
            "/campaigns",
            lambda: dashboard((RECORDS, renamed, created)[min(self.server.posts() // 2, 2)]),
        )
        with anyio.fail_after(TIMEOUT):
            async with self.session() as session:
                self.server.route("POST", UPDATE_URI, *mounted_replies())
                rename = await session.call(
                    "rename_campaign",
                    {"campaign_id": 7, "name": "  Renamed   Café & partners ", "confirm": True},
                )
                self.assertEqual(self.server.posts(), 2)
                self.server.route("POST", UPDATE_URI, *mounted_replies())
                create = await session.call(
                    "create_campaign", {"name": "Created campaign", "confirm": True}
                )
                self.assert_contained(session, network=True)
        self.assertEqual(payload(rename), {"id": 7, "name": renamed[0][1], "status": "Paused"})
        self.assertEqual(payload(create), {"id": 8, "name": "Created campaign", "status": "Draft"})
        calls = [
            r.json()["components"][0]["calls"][0]["method"]
            for r in self.server.requests
            if r.method == "POST"
        ]
        self.assertEqual(calls, ["mountAction", "callMountedAction"] * 2)

    async def test_blocked_write_is_an_error_without_retry_or_leaks(self) -> None:
        self.server.route("GET", "/campaigns", dashboard())
        body = f"<html><form>SECRET-BODY {COOKIE}</form></html>"
        with anyio.fail_after(TIMEOUT):
            async with self.session() as session:
                for status in (403, 419, 500):
                    with self.subTest(status=status):
                        self.server.route(
                            "POST", UPDATE_URI, Reply.html(body, status, cookies=SET_COOKIE)
                        )
                        before = self.server.posts()
                        result = await session.call(
                            "rename_campaign",
                            {"campaign_id": 7, "name": "Blocked rename", "confirm": True},
                        )
                        self.assertTrue(result.is_error, texts(result))
                        self.assertIn(str(status), texts(result))
                        self.assertEqual(self.server.posts() - before, 1, "no retries")
                self.assert_contained(session, network=True)

    async def test_tool_work_is_serialized_and_off_the_event_loop(self) -> None:
        slow = Concurrency(dashboard(), seconds=0.4)
        self.server.route_fn("GET", "/campaigns", slow)
        finished: list[str] = []
        with anyio.fail_after(TIMEOUT):
            async with self.session() as session:
                await session.client.list_tools()

                async def listing(label: str) -> None:
                    result = await session.call("list_campaigns")
                    self.assertFalse(result.is_error, texts(result))
                    finished.append(label)

                async with anyio.create_task_group() as group:
                    group.start_soon(listing, "first")
                    group.start_soon(listing, "second")
                    while not self.server.requests:
                        await anyio.sleep(0.02)
                    await session.client.list_tools(cache_mode="bypass")
                    finished.append("discovery")
                self.assert_contained(session, network=True)
        self.assertEqual(finished[0], "discovery", "tool work must not block the event loop")
        self.assertEqual(slow.peak, 1, "tool work must run one call at a time")


if __name__ == "__main__":
    unittest.main()
