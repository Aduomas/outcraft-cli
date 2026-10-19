"""Synthetic Livewire pages and update responses. No real Outcraft data."""

from __future__ import annotations

import html
import json

CSRF = "csrf-TOKEN-7f3a9c"
UPDATE_URI = "/livewire-8f3a/update"
CAMPAIGNS_NAME = "campaigns.index"

CAMPAIGNS_DATA = {
    "campaigns": [
        [
            {
                "id": 7,
                "name": "Q4 Café Outreach",
                "status": "active",
                "tags": [["b2b", "saas"], {"s": "arr"}],
            },
            {
                "id": 9,
                "name": "Re-engage / churned",
                "status": "paused",
                "tags": [[], {"s": "arr"}],
            },
        ],
        {"s": "arr"},
    ],
    "filters": [{"status": "active"}, {"s": "arr"}],
    "statuses": ["active", "paused"],
    "search": "",
    "page": 1,
}

CAMPAIGNS_UNWRAPPED = {
    "campaigns": [
        {"id": 7, "name": "Q4 Café Outreach", "status": "active", "tags": ["b2b", "saas"]},
        {"id": 9, "name": "Re-engage / churned", "status": "paused", "tags": []},
    ],
    "filters": {"status": "active"},
    "statuses": ["active", "paused"],
    "search": "",
    "page": 1,
}


def snapshot_json(
    name: str = CAMPAIGNS_NAME,
    data: dict | None = None,
    errors: object = None,
    checksum: str = "c0ffee",
    component_id: str = "lw-abc123",
) -> str:
    """PHP-style JSON: escaped slashes and \\u escapes, so any re-serialization changes the bytes."""
    payload = {
        "data": CAMPAIGNS_DATA if data is None else data,
        "memo": {
            "id": component_id,
            "name": name,
            "path": "campaigns",
            "method": "GET",
            "children": [],
            "scripts": [],
            "assets": [],
            "errors": [] if errors is None else errors,
            "locale": "en",
        },
        "checksum": checksum,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).replace("/", "\\/")


def component_html(
    snapshot: str | None, effects: dict | None = None, component_id: str = "lw-abc123"
) -> str:
    attrs = [f'wire:id="{component_id}"']
    if snapshot is not None:
        attrs.append(f'wire:snapshot="{html.escape(snapshot, quote=True)}"')
    if effects is not None:
        attrs.append(f'wire:effects="{html.escape(json.dumps(effects), quote=True)}"')
    return f"<div {' '.join(attrs)}><table><tr><td>Q4 Caf&eacute; Outreach</td></tr></table></div>"


def page_html(
    *components: str,
    csrf: str | None = CSRF,
    update_uri: str | None = UPDATE_URI,
    csrf_in: str = "script",
) -> str:
    meta = (
        f'<meta name="csrf-token" content="{csrf}">' if csrf and csrf_in in ("meta", "both") else ""
    )
    script_attrs = ['src="/livewire-8f3a/livewire.min.js?id=deadbeef"', 'data-navigate-once="true"']
    if csrf and csrf_in in ("script", "both"):
        script_attrs.append(f'data-csrf="{csrf}"')
    if update_uri is not None:
        script_attrs.append(f'data-update-uri="{html.escape(update_uri, quote=True)}"')
    return (
        "<!DOCTYPE html><html><head>"
        f'<meta charset="utf-8">{meta}<title>Campaigns</title></head><body>'
        f"{''.join(components)}"
        f"<script {' '.join(script_attrs)}></script>"
        "</body></html>"
    )


def campaigns_page(**kwargs: object) -> str:
    return page_html(component_html(snapshot_json(), effects={"listeners": ["refresh"]}), **kwargs)


def update_body(snapshot: str, effects: dict | None = None) -> dict:
    return {
        "components": [
            {
                "snapshot": snapshot,
                "effects": {"html": "<div>ok</div>"} if effects is None else effects,
            }
        ],
        "assets": [],
    }
