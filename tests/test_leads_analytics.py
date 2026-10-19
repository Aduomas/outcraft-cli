"""Leads and analytics reads follow the real table, lazy-load and filter contracts."""

from __future__ import annotations

import base64
import html
import io
import json
import unittest
from contextlib import redirect_stderr

from _campaign_fixtures import sent_component
from _fixtures import UPDATE_URI, page_html, snapshot_json, update_body
from _support import LocalServer, Reply

from outcraft_cli.arguments import parser
from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport


def volt(view: str) -> str:
    encoded = base64.b64encode(json.dumps({"name": view}).encode()).decode()
    return "volt-anonymous-fragment-" + encoded


def component(name: str, cid: str, inner: str = "", data: dict | None = None, **attrs: str) -> str:
    snapshot = snapshot_json(name=name, data=data or {"isTableLoaded": False}, component_id=cid)
    extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    escaped = html.escape(snapshot, quote=True)
    return f'<div wire:id="{cid}" wire:snapshot="{escaped}"{extra}>{inner}</div>'


def stack(*cells: str) -> str:
    cols = "".join(f'<div class="fi-ta-col"><div>{cell}</div></div>' for cell in cells)
    return f'<div class="fi-ta-stack fi-align-start">{cols}</div>'


def lead_row(lead_id: int, name: str, status: str) -> str:
    contact = stack(
        name,
        f'<span class="fi-copyable">{lead_id}@example.invalid</span>',
        '<span class="fi-copyable">+15550100</span>',
    )
    stacks = (
        contact
        + stack("United States", "America/Chicago")
        + stack("Status", f"<span>{status}</span>")
        + stack("Created", "2 days ago")
    )
    return f'<div wire:key="lw.table.records.{lead_id}">{stacks}</div>'


def table(rows: str, total: int) -> dict:
    footer = f'<span class="fi-pagination-overview">Showing 1 to 2 of {total:,} results</span>'
    return {"html": f"<div>{rows}{footer}</div>"}


def timeline_item(ago: str, day: str, time: str, details: str) -> str:
    return (
        '<div class="grid gap-6 grid-cols-[8rem_2.5rem_1fr]">'
        f"<div><div>{ago}</div><div>{day}</div><div>{time}</div></div>"
        f"<div><svg></svg></div><div>{details}</div></div>"
    )


def stat_block(title: str, pairs: list[tuple[str, str]]) -> str:
    cells = "".join(
        f'<div><span>{label}</span><div class="text-3xl font-medium">{value}</div></div>'
        for label, value in pairs
    )
    return f"<section><h3>{title}</h3><p>Shows how things move.</p>{cells}</section>"


class LeadsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))
        data = {"selectedFilters": []}
        self.listing = snapshot_json(name="leads.leads-page", data=data, component_id="leads")
        page = page_html(component("leads.leads-page", "leads", data=data))
        self.server.route("GET", "/campaigns/7/leads", Reply.html(page))

    def sent(self) -> list[dict]:
        return [sent_component(r) for r in self.server.requests_to("POST", UPDATE_URI)]

    def test_list_filters_and_sizes_first_then_paginates_with_the_returned_snapshot(self) -> None:
        loaded = snapshot_json(name="leads.leads-page", data={"page": 1}, component_id="leads")
        rows = lead_row(501, "Synthetic Ada", "In outreach") + lead_row(502, "Synthetic Bo", "Idle")
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(update_body(loaded, table("", 37))),
            Reply.json(update_body(self.listing, table(rows, 37))),
        )

        result = self.client.leads.list(7, "in_outreach", page=2, per_page=10)

        first, second = self.sent()
        self.assertEqual(first["calls"][0]["method"], "loadTable")
        self.assertEqual(
            first["updates"], {"selectedFilters": ["status:in_outreach"], "tableRecordsPerPage": 10}
        )
        self.assertEqual(second["snapshot"], loaded, "paginate on the freshly signed snapshot")
        self.assertEqual(second["calls"][0]["method"], "gotoPage")
        self.assertEqual(second["calls"][0]["params"], [2, "page"])
        self.assertEqual(result["total"], 37)
        self.assertEqual(
            result["leads"][0],
            {
                "id": 501,
                "name": "Synthetic Ada",
                "email": "501@example.invalid",
                "phone": "+15550100",
                "country": "United States",
                "timezone": "America/Chicago",
                "status": "In outreach",
                "created": "2 days ago",
            },
        )
        self.assertEqual(result["leads"][1]["status"], "Idle")

    def test_first_page_makes_one_request(self) -> None:
        self.server.route("POST", UPDATE_URI, Reply.json(update_body(self.listing, table("", 0))))
        self.client.leads.list(7)
        self.assertEqual(len(self.sent()), 1)

    def test_label_like_names_and_numbers_do_not_corrupt_fields_or_total(self) -> None:
        rows = lead_row(601, "Status", "Idle") + lead_row(602, "Director of 2 results", "Idle")
        self.server.route(
            "POST", UPDATE_URI, Reply.json(update_body(self.listing, table(rows, 400)))
        )

        result = self.client.leads.list(7)

        self.assertEqual(result["total"], 400)
        self.assertEqual(result["leads"][0]["name"], "Status")
        self.assertEqual(result["leads"][0]["country"], "United States")
        self.assertEqual(result["leads"][0]["status"], "Idle")

    def test_counts_derive_idle_without_the_idle_filter(self) -> None:
        counts = [5, 0, 0, 0, 2, 0, 0, 0, 40]  # 8 non-idle statuses, then all leads
        replies = [Reply.json(update_body(self.listing, table("", n))) for n in counts]
        self.server.route("POST", UPDATE_URI, *replies)

        result = self.client.leads.counts(7)

        filters = [sent["updates"]["selectedFilters"] for sent in self.sent()]
        self.assertNotIn(["status:idle"], filters)
        self.assertEqual(filters[-1], [])
        self.assertEqual(result["total"], 40)
        self.assertEqual(result["statuses"], {"idle": 33, "in_outreach": 5, "disqualified": 2})
        self.assertEqual(len(self.server.requests_to("GET", "/campaigns/7/leads")), 1)

    def test_list_rejects_unknown_status_before_any_request(self) -> None:
        with self.assertRaises(OutcraftError):
            self.client.leads.list(7, "maybe")
        self.assertEqual(self.server.requests, [])

    def test_show_reads_runs_and_one_item_per_interaction(self) -> None:
        timeline = timeline_item(
            "1 day ago", "Oct 17, 2026", "18:36:15", "Email: let us meet Oct 22, 2026 12:00:00"
        ) + timeline_item("2 days ago", "Oct 16, 2026", "18:35:24", "Call: no answer")
        page = page_html(
            component(volt("lead.edit"), "runs", data={"isTableLoaded": False, "lead": 5}),
            component("leads.lead-contact-details-infolist", "info", "Lead Details Name Synthetic"),
            component("lead-interactions-timeline", "timeline", timeline, data={"lead": 5}),
        )
        self.server.route("GET", "/leads/5", Reply.html(page))
        run = (
            '<div wire:key="lw.table.records.900"><div class="fi-ta-text-item">Synthetic campaign'
            '</div><span class="fi-badge">In progress</span>'
            '<div class="fi-ta-text-item">First Interaction</div>'
            '<div class="fi-ta-text-item">No decision</div><div class="fi-ta-text-item">Created</div>'
            '<div class="fi-ta-text-item">1 week ago</div></div>'
        )
        runs = snapshot_json(
            name=volt("lead.edit"), data={"isTableLoaded": True}, component_id="runs"
        )
        self.server.route("POST", UPDATE_URI, Reply.json(update_body(runs, {"html": run})))

        result = self.client.leads.show(5)

        self.assertEqual(
            result["runs"][0],
            {
                "id": 900,
                "campaign": "Synthetic campaign",
                "status": "In progress",
                "first_interaction": "No decision",
                "follow_up": None,
                "created": "1 week ago",
            },
        )
        self.assertEqual(
            len(result["interactions"]), 2, "a date inside a message is not a new item"
        )
        self.assertEqual(result["interactions"][0]["at"], "Oct 17, 2026 18:36:15")
        self.assertEqual(result["interactions"][1]["details"], "Call: no answer")


class AnalyticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))

    def stats(self, suffix: str) -> str:
        token = f"$wire.__lazyLoad(&#039;TOKEN{suffix}&#039;)"
        name = "v2.analytics.campaign-core-stats"
        return component(name, f"core{suffix}", data={"range": "7d"}, x_intersect=token)

    def loaded(self, suffix: str, block: str) -> Reply:
        name = "v2.analytics.campaign-core-stats"
        snapshot = snapshot_json(name=name, data={"range": "7d"}, component_id=f"core{suffix}")
        return Reply.json(update_body(snapshot, {"html": block}))

    def test_overview_lazy_loads_each_block_and_parses_metrics(self) -> None:
        filters = component(volt("analytics.campaign.overview"), "filters")
        page = page_html(filters, self.stats("A"))
        self.server.route("GET", "/campaigns/7/analytics", Reply.html(page))
        block = stat_block(
            "Performance Funnel", [("Campaign Runs", "251"), ("Engaged (48.2%)", "121")]
        )
        self.server.route("POST", UPDATE_URI, self.loaded("A", block))

        result = self.client.analytics.overview(7)

        sent = sent_component(self.server.requests_to("POST", UPDATE_URI)[0])
        self.assertEqual(
            sent["calls"][0], {"method": "__lazyLoad", "params": ["TOKENA"], "metadata": {}}
        )
        metrics = {"campaign_runs": {"value": 251}, "engaged": {"value": 121, "percent": 48.2}}
        self.assertEqual(result["blocks"], [{"title": "Performance Funnel", "metrics": metrics}])

    def test_range_reads_blocks_returned_only_as_partials(self) -> None:
        filters = component(
            volt("analytics.overview"), "filters", '<div wire:partial="stats"></div>'
        )
        self.server.route("GET", "/analytics", Reply.html(page_html(filters, self.stats("A"))))
        refreshed = snapshot_json(
            name=volt("analytics.overview"), data={"range": "30d"}, component_id="filters"
        )
        block = stat_block("Revenue Breakdown", [("Total Revenue", "$11,217")])
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(update_body(refreshed, {"partials": {"stats": self.stats("B")}})),
            self.loaded("B", block),
        )

        result = self.client.analytics.overview(None, "30d")

        first, second = [sent_component(r) for r in self.server.requests_to("POST", UPDATE_URI)]
        self.assertEqual(first["updates"], {"range": "30d"})
        self.assertEqual(first["calls"][0]["method"], "$refresh")
        self.assertEqual(second["calls"][0]["params"], ["TOKENB"])
        self.assertEqual(result["blocks"][0]["metrics"], {"total_revenue": {"value": 11217}})

    def test_cli_rejects_an_unknown_range(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            parser().parse_args(["analytics", "--range", "90d"])


if __name__ == "__main__":
    unittest.main()
