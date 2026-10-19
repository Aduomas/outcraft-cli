from __future__ import annotations

import base64
import json
import re

from .errors import OutcraftError
from .html import Component, Node
from .livewire import Livewire

LEAD_STATUSES = (
    "idle",
    "in_outreach",
    "handoff_requested",
    "review_required",
    "unreachable",
    "disqualified",
    "requested_not_to_contact",
    "blacklisted",
    "redacted",
)
PER_PAGE = (10, 25, 100)
RECORD_KEY = re.compile(r"\.table\.records\.(\d+)$")
TOTAL = re.compile(r"of\s+([\d,]+)\s+results")
INTERACTION_START = re.compile(
    r"(?:\b\d+ \w+ ago )?(?=\b[A-Z][a-z]{2} \d{1,2}, \d{4} \d{2}:\d{2}:\d{2}\b)"
)


def leads_path(campaign_id: int | None) -> str:
    if campaign_id is not None and campaign_id < 1:
        raise OutcraftError("Use a positive campaign ID.")
    return f"/campaigns/{campaign_id}/leads" if campaign_id else "/leads"


class Leads:
    def __init__(self, livewire: Livewire) -> None:
        self.livewire = livewire

    def list(
        self,
        campaign_id: int | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, object]:
        if status is not None and status not in LEAD_STATUSES:
            raise OutcraftError("Unknown lead status; use one of " + ", ".join(LEAD_STATUSES))
        if per_page not in PER_PAGE or page < 1:
            raise OutcraftError("Use page 1 or higher and per_page 10, 25 or 100.")
        root = self._table(campaign_id, status, page, per_page)
        records = lead_records(root)
        return {
            "campaign_id": campaign_id,
            "status": status,
            "page": page,
            "per_page": per_page,
            "total": result_total(root, len(records)),
            "leads": records,
        }

    def counts(self, campaign_id: int | None = None) -> dict[str, object]:
        listing = self.livewire.page(leads_path(campaign_id))
        component = listing.component("leads.leads-page")

        def total(status: str | None) -> int:
            updates: dict[str, object] = {"selectedFilters": [f"status:{status}"] if status else []}
            root = self.livewire.call(listing, component, "loadTable", updates=updates).root
            return result_total(root, len(records(root)))

        # Outcraft's own idle filter times out with HTTP 500; statuses are exclusive, so derive it.
        statuses = {status: total(status) for status in LEAD_STATUSES if status != "idle"}
        all_leads = total(None)
        statuses = {"idle": all_leads - sum(statuses.values()), **statuses}
        return {
            "campaign_id": campaign_id,
            "total": all_leads,
            "statuses": {k: v for k, v in statuses.items() if v},
        }

    def show(self, lead_id: int) -> dict[str, object]:
        if lead_id < 1:
            raise OutcraftError("Use a positive lead ID.")
        page = self.livewire.page(f"/leads/{lead_id}")
        details = next(
            (c for c in page.components if c.name.endswith("contact-details-infolist")), None
        )
        timeline = next(
            (c for c in page.components if c.name == "lead-interactions-timeline"), None
        )
        runs_component = next((c for c in page.components if fragment_name(c) == "lead.edit"), None)
        if details is None or timeline is None or runs_component is None:
            raise OutcraftError("Lead page markup changed; cannot read this lead safely.")
        runs = self.livewire.call(page, runs_component, "loadTable").root
        return {
            "id": lead_id,
            "details": details.root.text,
            "runs": run_records(runs),
            "interactions": [
                part.strip() for part in INTERACTION_START.split(timeline.root.text) if part.strip()
            ],
        }

    def _table(self, campaign_id: int | None, status: str | None, page: int, per_page: int) -> Node:
        listing = self.livewire.page(leads_path(campaign_id))
        component = listing.component("leads.leads-page")
        updates: dict[str, object] = {
            "selectedFilters": [f"status:{status}"] if status else [],
            "tableRecordsPerPage": per_page,
            "paginators.page": page,
        }
        return self.livewire.call(listing, component, "loadTable", updates=updates).root


def fragment_name(component: Component) -> str | None:
    """Volt anonymous components encode their view name as base64 JSON in the component name."""
    prefix = "volt-anonymous-fragment-"
    if not component.name.startswith(prefix):
        return None
    encoded = component.name[len(prefix) :]
    try:
        return str(json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))["name"])
    except (ValueError, KeyError, TypeError):
        return None


def records(root: Node) -> list[tuple[str, Node]]:
    found = []
    for node in root.find():
        match = RECORD_KEY.search(node.attrs.get("wire:key", ""))
        if match:
            found.append((match[1], node))
    return found


def result_total(root: Node, shown: int) -> int:
    match = TOTAL.search(root.text)
    return int(match[1].replace(",", "")) if match else shown


def lead_records(root: Node) -> list[dict[str, object]]:
    result = []
    for key, node in records(root):
        copyable = [n.text for n in node.find("span") if "fi-copyable" in n.attrs.get("class", "")]
        texts = [
            n.text
            for n in node.find()
            if "fi-ta-text-item" in n.attrs.get("class", "")
            and n.text not in ("Status", "Created")
            and not any(value in n.text for value in copyable)
        ]
        labelled = labelled_values(node)
        result.append(
            {
                "id": int(key),
                "name": texts[0] if texts else "",
                "email": next((v for v in copyable if "@" in v), None),
                "phone": next((v for v in copyable if v.startswith("+")), None),
                "country": texts[1] if len(texts) > 1 else None,
                "status": labelled.get("Status"),
                "created": labelled.get("Created"),
            }
        )
    return result


def labelled_values(node: Node) -> dict[str, str]:
    values: dict[str, str] = {}
    flat = [n for n in node.find() if n.tag in ("div", "span") and n.text]
    for index, item in enumerate(flat[:-1]):
        if item.text in ("Status", "Created") and item.text not in values:
            later = [n.text for n in flat[index + 1 :] if n.text != item.text]
            if later:
                values[item.text] = later[0]
    return values


def run_records(root: Node) -> list[dict[str, object]]:
    result = []
    for key, node in records(root):
        cells = [
            n.text
            for n in node.find()
            if "fi-ta-text-item" in n.attrs.get("class", "")
            or "fi-badge" in n.attrs.get("class", "")
        ]
        result.append(
            {
                "id": int(key),
                "campaign": cells[0] if cells else "",
                "status": cells[1] if len(cells) > 1 else None,
                "first_interaction": after(cells, "First Interaction"),
                "follow_up": after(cells, "Follow-up"),
                "created": after(cells, "Created"),
            }
        )
    return result


def after(cells: list[str], label: str) -> str | None:
    """Return the value that follows a label cell, skipping the duplicate badge text."""
    if label not in cells:
        return None
    rest = cells[cells.index(label) + 1 :]
    return rest[0] if rest else None
