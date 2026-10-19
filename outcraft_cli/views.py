from __future__ import annotations

import re

from .errors import OutcraftError
from .html import Component, Node, Page

SECTIONS = {
    "source": "",
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
    return f"/campaigns/{campaign_id}/onboarding/{SECTIONS[section]}".rstrip("/")


def campaigns_from_page(page: Page) -> list[dict[str, object]]:
    campaigns: dict[int, dict[str, object]] = {}
    for node in page.root.find():
        link = (
            re.search(r"/campaigns/(\d+)/onboarding/?$", node.attrs.get("href", ""))
            if node.tag == "a"
            else None
        )
        card = re.search(r"\.table\.records\.(\d+)$", node.attrs.get("wire:key", ""))
        archived = card is not None and any(
            "mountAction('unarchive'" in value or 'mountAction("unarchive"' in value
            for child in node.owned()
            for key, value in child.attrs.items()
            if key.startswith("wire:click")
        )
        match = card if archived else link
        if match is None:
            continue
        campaign_id = int(match[1])
        names = [n.text for n in node.owned("li") if "fi-ta-text-item" in n.attrs.get("class", "")]
        if not names and archived:
            names = [
                n.text
                for n in node.owned()
                if "fi-ta-text-item" in n.attrs.get("class", "").split()
            ]
        badges = [n.text for n in node.owned("span") if "fi-badge" in n.attrs.get("class", "")]
        status = "Archived" if archived else badges[-1] if badges else "Unknown"
        if not names:
            raise OutcraftError(
                "Campaign card markup changed; cannot safely identify campaign names."
            )
        campaigns[campaign_id] = {"id": campaign_id, "name": names[0], "status": status}
    return list(campaigns.values())


def table_rows(root: Node) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in root.owned("tr"):
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
