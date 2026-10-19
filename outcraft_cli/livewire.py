from __future__ import annotations

import json

from .errors import AuthenticationRequired, OutcraftError
from .html import Component, Page, object_dict, parse_page
from .rendering import render_effects
from .transport import Transport


class Livewire:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def page(self, path: str) -> Page:
        response = self.transport.request("GET", path)
        page = parse_page(response.text)
        page.url = response.url
        if not page.csrf or not page.update_uri:
            raise AuthenticationRequired(
                "No Livewire handshake found. Login or a browser challenge may be required."
            )
        self.transport.url(page.update_uri)
        return page

    def call(
        self,
        page: Page,
        component: Component,
        method: str,
        params: list[object] | None = None,
        updates: dict[str, object] | None = None,
    ) -> Component:
        if not page.csrf or not page.update_uri:
            raise OutcraftError("Missing Livewire CSRF token or update URI.")
        payload = {
            "_token": page.csrf,
            "components": [
                {
                    "snapshot": component.snapshot,
                    "updates": updates or {},
                    "calls": [{"method": method, "params": params or [], "metadata": {}}],
                }
            ],
        }
        response = self.transport.request("POST", page.update_uri, payload)
        try:
            body = object_dict(json.loads(response.text))
            replies = body.get("components")
            if not isinstance(replies, list) or len(replies) != 1:
                raise OutcraftError(
                    "Expected 1 updated Livewire component; verify state before retrying."
                )
            reply = object_dict(replies[0])
            snapshot = reply.get("snapshot")
            if not isinstance(snapshot, str):
                raise OutcraftError("Missing signed snapshot in Livewire response.")
            effects = object_dict(reply.get("effects", {}))
            result = Component(snapshot, effects, component.root)
        except (ValueError, KeyError, TypeError):
            raise OutcraftError(
                "Invalid Livewire response; verify state before retrying."
            ) from None
        if result.memo.get("id") != component.memo.get("id") or result.name != component.name:
            raise OutcraftError(
                "Livewire returned a different component; verify state before retrying."
            )
        errors = result.memo.get("errors") or body.get("errors") or effects.get("errors")
        if errors:
            fields = (
                ", ".join(str(k) for k in errors)
                if isinstance(errors, dict)
                else "submitted fields"
            )
            raise OutcraftError(f"Validation failed for: {fields}.")
        result.root = render_effects(component.root, effects, component.memo.get("id"))
        return result
