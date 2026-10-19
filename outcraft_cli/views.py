from __future__ import annotations

import re

from .errors import OutcraftError
from .html import Component, Node, Page

SECTIONS = {
    "source": "apps/api",
    "brand": "company",
    "instructions": "campaign/context",
    "general": "campaign/general",
    "schedule": "campaign/schedule",
    "channels": "campaign/channels",
    "sequence": "campaign/sequence",
    "evaluations": "campaign/evaluations",
    "conditions": "conditions",
    "agent": "agent",
    "finish": "finish",
    "countries": "countries",
    "overrides": "overrides",
}


def section_path(campaign_id: int, section: str) -> str:
    if campaign_id < 1 or section not in SECTIONS:
        raise OutcraftError(
            "Use a positive campaign ID and a section listed in campaigns sections."
        )
    return f"/campaigns/{campaign_id}/onboarding/{SECTIONS[section]}"


def campaigns_from_page(page: Page) -> list[dict[str, object]]:
    campaigns: dict[int, dict[str, object]] = {}
    for link in page.root.find("a"):
        match = re.search(r"/campaigns/(\d+)/onboarding/?$", link.attrs.get("href", ""))
        if not match:
            continue
        campaign_id = int(match[1])
        names = [n.text for n in link.find("li") if "fi-ta-text-item" in n.attrs.get("class", "")]
        badges = [n.text for n in link.find("span") if "fi-badge" in n.attrs.get("class", "")]
        status = next(
            (t for t in badges if t in ("Running", "Paused", "Draft", "Archived", "Setup")),
            "Unknown",
        )
        if not names:
            raise OutcraftError(
                "Campaign card markup changed; cannot safely identify campaign names."
            )
        campaigns[campaign_id] = {"id": campaign_id, "name": names[0], "status": status}
    return list(campaigns.values())


def table_rows(root: Node) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in root.find("tr"):
        keys: set[str] = set()
        for node in row.find():
            for value in node.attrs.values():
                keys.update(
                    re.findall(r'"recordKey"\s*:\s*"([^\"]+)"', value.replace(r"\u0022", '"'))
                )
        rows.append(
            {
                "record_keys": sorted(keys),
                "cells": [
                    n.text for n in row.children if isinstance(n, Node) and n.tag in ("td", "th")
                ],
            }
        )
    return rows


def describe(component: Component) -> dict[str, object]:
    return {
        "name": component.name,
        "data": component.data,
        "actions": component.actions,
        "fields": component.fields,
        "listeners": component.effects.get("listeners", []),
        "rows": table_rows(component.root),
    }


def form_component(page: Page) -> Component:
    matches = []
    for component in page.components:
        listeners = component.effects.get("listeners")
        if (
            isinstance(component.data.get("data"), dict)
            and isinstance(listeners, list)
            and "move-to-next-step" in listeners
        ):
            matches.append(component)
    if len(matches) != 1:
        raise OutcraftError(
            "This section is action-driven. Use inspect and campaigns action instead of update."
        )
    return matches[0]


def require_confirmation(confirm: bool) -> None:
    if not confirm:
        raise OutcraftError(
            "Mutation refused. Add --yes to execute, or --dry-run to inspect the plan."
        )


def valid_name(name: str) -> None:
    if not 3 <= len(name.strip()) <= 191:
        raise OutcraftError("Campaign names must contain 3 to 191 characters.")


def lookup(data: dict[str, object], path: str) -> object:
    value: object = data
    for key in path.split("."):
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise OutcraftError(
                f"Unknown field: {path}. Export the current section before editing."
            )
    return value
