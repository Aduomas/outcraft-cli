"""Synthetic DOM-backed campaign pages, without a snapshot campaigns array."""

from __future__ import annotations

import html

from _fixtures import component_html, page_html, snapshot_json
from _support import LocalServer, Recorded

LIST_NAME = "synthetic.campaign-list"
LOGIN_NAME = "synthetic.login"
LIST_DATA = {"tableSearch": "", "mountedActions": [[], {"s": "arr"}]}


def list_snapshot(checksum: str = "list-original", **extra: object) -> str:
    return snapshot_json(name=LIST_NAME, data={**LIST_DATA, **extra}, checksum=checksum)


def campaign_page(
    records: tuple[tuple[int, str, str], ...] = ((7, "Synthetic campaign", "Paused"),),
    snapshot: str | None = None,
) -> str:
    cards = "".join(
        f'<a href="/campaigns/{identifier}/onboarding">'
        f'<li class="fi-ta-text-item">{html.escape(name)}</li>'
        f'<span class="fi-badge">{html.escape(status)}</span></a>'
        for identifier, name, status in records
    )
    target = component_html(snapshot or list_snapshot()).replace("</div>", cards + "</div>")
    navigation = component_html(
        snapshot_json(name="synthetic.navigation", data={"open": False}, component_id="nav"),
        component_id="nav",
    )
    return page_html(navigation, target)


def login_snapshot(**kwargs: object) -> str:
    return snapshot_json(
        name=LOGIN_NAME, data={"email": "", "password": "", "rememberMe": False}, **kwargs
    )


def login_page() -> str:
    unrelated = component_html(
        snapshot_json(name="synthetic.banner", data={"email": ""}, component_id="banner"),
        component_id="banner",
    )
    return page_html(unrelated, component_html(login_snapshot()))


def sent_component(request: Recorded) -> dict:
    body = request.json()
    components = body["components"]
    if len(components) != 1:
        raise AssertionError(f"Expected 1 target component, received {len(components)}")
    return components[0]


def request_order(server: LocalServer) -> list[tuple[str, str]]:
    return [(request.method, request.path.split("?", 1)[0]) for request in server.requests]
