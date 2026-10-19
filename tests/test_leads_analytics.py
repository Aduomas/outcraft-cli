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
    return (
        "volt-anonymous-fragment-" + base64.b64encode(json.dumps({"name": view}).encode()).decode()
    )


def component(
    name: str, component_id: str, inner: str = "", data: dict | None = None, **attrs: str
) -> str:
    snapshot = snapshot_json(
        name=name, data=data or {"isTableLoaded": False}, component_id=component_id
    )
    extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return (
        f'<div wire:id="{component_id}" wire:snapshot="{html.escape(snapshot, quote=True)}"{extra}>'
        f"{inner}</div>"
    )


def lead_row(lead_id: int, name: str, status: str) -> str:
    return (
        f'<div wire:key="lw.table.records.{lead_id}">'
        f'<div class="fi-ta-text-item fi-font-medium">{name}</div>'
        f'<div class="fi-ta-text-item"><span class="fi-copyable">{name.lower()}@example.invalid</span></div>'
        '<div class="fi-ta-text-item"><span class="fi-copyable">+15550100</span></div>'
        '<div class="fi-ta-text-item">United States</div>'
        f'<div class="fi-ta-text-item">Status</div><span>{status}</span>'
        '<div class="fi-ta-text-item">Created</div><span>2 days ago</span></div>'
    )


def table(rows: str, total: int) -> dict:
    return {"html": f"<div>{rows}<p>Showing 1 to 2 of {total:,} results</p></div>"}


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
        self.listing = snapshot_json(
            name="leads.leads-page", data={"selectedFilters": []}, component_id="leads"
        )
        self.server.route(
            "GET",
            "/campaigns/7/leads",
            Reply.html(
                page_html(component("leads.leads-page", "leads", data={"selectedFilters": []}))
            ),
        )

    def test_list_sends_filter_page_size_and_parses_rows(self) -> None:
        rows = lead_row(501, "Synthetic Ada", "In outreach") + lead_row(502, "Synthetic Bo", "Idle")
        self.server.route(
            "POST", UPDATE_URI, Reply.json(update_body(self.listing, table(rows, 37)))
        )

        result = self.client.leads.list(7, "in_outreach", page=2, per_page=10)

        sent = sent_component(self.server.requests_to("POST", UPDATE_URI)[0])
        self.assertEqual(sent["calls"][0]["method"], "loadTable")
        self.assertEqual(
            sent["updates"],
            {
                "selectedFilters": ["status:in_outreach"],
                "tableRecordsPerPage": 10,
                "paginators.page": 2,
            },
        )
        self.assertEqual(result["total"], 37)
        self.assertEqual(
            result["leads"][0],
            {
                "id": 501,
                "name": "Synthetic Ada",
                "email": "synthetic ada@example.invalid",
                "phone": "+15550100",
                "country": "United States",
                "status": "In outreach",
                "created": "2 days ago",
            },
        )
        self.assertEqual(result["leads"][1]["status"], "Idle")

    def test_counts_derive_idle_without_the_failing_idle_filter(self) -> None:
        counts = [5, 0, 0, 0, 2, 0, 0, 0, 40]  # 8 non-idle statuses, then all leads
        self.server.route(
            "POST",
            UPDATE_URI,
            *[Reply.json(update_body(self.listing, table("", n))) for n in counts],
        )

        result = self.client.leads.counts(7)

        filters = [
            sent_component(r)["updates"]["selectedFilters"]
            for r in self.server.requests_to("POST", UPDATE_URI)
        ]
        self.assertNotIn(["status:idle"], filters)
        self.assertEqual(filters[-1], [])
        self.assertEqual(result["total"], 40)
        self.assertEqual(result["statuses"], {"idle": 33, "in_outreach": 5, "disqualified": 2})
        self.assertEqual(len(self.server.requests_to("GET", "/campaigns/7/leads")), 1)

    def test_list_rejects_unknown_status_before_any_request(self) -> None:
        with self.assertRaises(OutcraftError):
            self.client.leads.list(7, "maybe")
        self.assertEqual(self.server.requests, [])

    def test_show_reads_runs_and_splits_interactions(self) -> None:
        timeline = (
            "1 day ago Oct 17, 2026 18:36:15 Campaign Synthetic Email Outbound Hello "
            "2 days ago Oct 16, 2026 18:35:24 Campaign Synthetic Call Outbound No answer"
        )
        self.server.route(
            "GET",
            "/leads/5",
            Reply.html(
                page_html(
                    component(volt("lead.edit"), "runs", data={"isTableLoaded": False, "lead": 5}),
                    component(
                        "leads.lead-contact-details-infolist", "info", "Lead Details Name Synthetic"
                    ),
                    component("lead-interactions-timeline", "timeline", timeline, data={"lead": 5}),
                )
            ),
        )
        run = (
            '<div wire:key="lw.table.records.900"><div class="fi-ta-text-item">Synthetic campaign</div>'
            '<span class="fi-badge">In progress</span><div class="fi-ta-text-item">First Interaction</div>'
            '<div class="fi-ta-text-item">No decision</div><div class="fi-ta-text-item">Created</div>'
            '<div class="fi-ta-text-item">1 week ago</div></div>'
        )
        runs_snapshot = snapshot_json(
            name=volt("lead.edit"), data={"isTableLoaded": True}, component_id="runs"
        )
        self.server.route("POST", UPDATE_URI, Reply.json(update_body(runs_snapshot, {"html": run})))

        result = self.client.leads.show(5)

        self.assertEqual(result["runs"][0]["campaign"], "Synthetic campaign")
        self.assertEqual(result["runs"][0]["status"], "In progress")
        self.assertEqual(result["runs"][0]["first_interaction"], "No decision")
        self.assertEqual(result["runs"][0]["created"], "1 week ago")
        self.assertEqual(len(result["interactions"]), 2)
        self.assertTrue(result["interactions"][1].startswith("Oct 16, 2026"))


class AnalyticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LocalServer().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = Client(Transport(base_url=self.server.base_url, session_file=None))

    def stats(self, suffix: str) -> str:
        return component(
            "v2.analytics.campaign-core-stats",
            f"core{suffix}",
            data={"range": "7d"},
            x_intersect=f"$wire.__lazyLoad(&#039;TOKEN{suffix}&#039;)",
        )

    def test_overview_lazy_loads_each_block_and_parses_metrics(self) -> None:
        self.server.route(
            "GET",
            "/campaigns/7/analytics",
            Reply.html(
                page_html(
                    component(volt("analytics.campaign.overview"), "filters"), self.stats("A")
                )
            ),
        )
        block = stat_block(
            "Performance Funnel", [("Campaign Runs", "251"), ("Engaged (48.2%)", "121")]
        )
        loaded = snapshot_json(
            name="v2.analytics.campaign-core-stats", data={"range": "7d"}, component_id="coreA"
        )
        self.server.route("POST", UPDATE_URI, Reply.json(update_body(loaded, {"html": block})))

        result = self.client.analytics.overview(7)

        sent = sent_component(self.server.requests_to("POST", UPDATE_URI)[0])
        self.assertEqual(
            sent["calls"][0], {"method": "__lazyLoad", "params": ["TOKENA"], "metadata": {}}
        )
        self.assertEqual(
            result["blocks"],
            [
                {
                    "title": "Performance Funnel",
                    "metrics": {
                        "campaign_runs": {"value": 251},
                        "engaged": {"value": 121, "percent": 48.2},
                    },
                }
            ],
        )

    def test_range_refreshes_filters_then_loads_the_rerendered_block(self) -> None:
        self.server.route(
            "GET",
            "/analytics",
            Reply.html(
                page_html(component(volt("analytics.overview"), "filters"), self.stats("A"))
            ),
        )
        filters = snapshot_json(
            name=volt("analytics.overview"), data={"range": "30d"}, component_id="filters"
        )
        loaded = snapshot_json(
            name="v2.analytics.campaign-core-stats", data={"range": "30d"}, component_id="coreB"
        )
        block = stat_block("Revenue Breakdown", [("Total Revenue", "$11,217")])
        self.server.route(
            "POST",
            UPDATE_URI,
            Reply.json(update_body(filters, {"html": f"<div>{self.stats('B')}</div>"})),
            Reply.json(update_body(loaded, {"html": block})),
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
