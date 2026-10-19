from __future__ import annotations

import re
from urllib.parse import urlsplit

from .actions import find_action_component, mounted_action
from .campaign_index import CampaignIndex
from .errors import OutcraftError
from .html import Component, object_dict
from .livewire import Livewire
from .transport import Transport
from .views import (
    SECTIONS,
    describe,
    form_component,
    lookup,
    require_confirmation,
    section_path,
    valid_name,
)


class Client:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.livewire = Livewire(transport)
        self.index = CampaignIndex(self.livewire)

    def login(self, email: str, password: str) -> dict[str, object]:
        if not email or not password:
            raise OutcraftError(
                "Set OUTCRAFT_EMAIL and OUTCRAFT_PASSWORD locally, then run auth login."
            )
        page = self.livewire.page("/auth/login")
        if urlsplit(page.url).path.rstrip("/") == "/campaigns" and any(
            "tableSearch" in c.data for c in page.components
        ):
            return self.status()
        matches = [c for c in page.components if "email" in c.data and "password" in c.data]
        if len(matches) != 1:
            raise OutcraftError("Cannot identify 1 email/password login component.")
        result = self.livewire.call(
            page,
            matches[0],
            "authenticate",
            updates={"email": email, "password": password, "rememberMe": True},
        )
        redirect = result.effects.get("redirect")
        if isinstance(redirect, str):
            self.transport.request("GET", redirect)
        self.index.page()
        return {"authenticated": True, "origin": self.transport.base_url}

    def ensure_authenticated(self, email: str = "", password: str = "") -> None:
        page = self.livewire.page("/campaigns")
        if "/auth/" in urlsplit(page.url).path or any(
            "password" in c.data for c in page.components
        ):
            self.login(email, password)

    def campaigns(self, archived: bool = False, page: int = 1) -> list[dict[str, object]]:
        return self.index.items(archived, page)

    def status(self) -> dict[str, object]:
        self.index.page()
        return {"authenticated": True, "origin": self.transport.base_url}

    def inspect(self, campaign_id: int, section: str = "instructions") -> dict[str, object]:
        path = section_path(campaign_id, section)
        return self.inspect_path(path)

    def inspect_path(self, path: str) -> dict[str, object]:
        page = self.livewire.page(path)
        return {
            "url": page.url,
            "components": [describe(c) for c in page.components],
            "links": [
                {"label": n.text, "href": n.attrs["href"]}
                for n in page.root.find("a")
                if "href" in n.attrs
            ],
        }

    def export(self, campaign_id: int) -> dict[str, object]:
        return {"id": campaign_id, "sections": {s: self.inspect(campaign_id, s) for s in SECTIONS}}

    def create(self, name: str, confirm: bool = False) -> dict[str, object]:
        require_confirmation(confirm)
        name = valid_name(name)
        page = self.index.page()
        result = mounted_action(
            self.livewire,
            page,
            page.component(key="tableSearch"),
            "createNewCampaign",
            {"name": name},
        )
        return self._created(result, name)

    def _created(self, result: Component, name: str | None = None) -> dict[str, object]:
        redirect = result.effects.get("redirect")
        match = None
        if isinstance(redirect, str):
            match = re.search(r"/campaigns/(\d+)(?:/|$)", self.transport.url(redirect))
        if match is None:
            raise OutcraftError("New campaign identity was not returned. Inspect before retrying.")
        record = self.index.locate(int(match[1]))[1]
        if name is not None and record["name"] != name:
            raise OutcraftError("New campaign name was not verified. Inspect before retrying.")
        return record

    def rename(self, campaign_id: int, name: str, confirm: bool = False) -> dict[str, object]:
        require_confirmation(confirm)
        name = valid_name(name)
        section_path(campaign_id, "finish")
        page, _ = self.index.locate(campaign_id)
        mounted_action(
            self.livewire,
            page,
            page.component(key="tableSearch"),
            "edit_name",
            {"name": name},
            str(campaign_id),
        )
        result = self.index.locate(campaign_id)[1]
        if result["name"] != name:
            raise OutcraftError("Rename did not persist. Inspect the campaign before retrying.")
        return result

    def archive(self, campaign_id: int, confirm: bool = False) -> dict[str, object]:
        require_confirmation(confirm)
        section_path(campaign_id, "finish")
        page, _ = self.index.locate(campaign_id)
        mounted_action(
            self.livewire,
            page,
            page.component(key="tableSearch"),
            "archive",
            record=str(campaign_id),
        )
        if self.index.contains(campaign_id):
            raise OutcraftError("Campaign is still in the active list; archive was not verified.")
        record = self.index.locate(campaign_id, archived=True)[1]
        return {**record, "operation": "archive", "permanently_deleted": False}

    def duplicate(self, campaign_id: int, confirm: bool = False) -> dict[str, object]:
        require_confirmation(confirm)
        section_path(campaign_id, "finish")
        page, _ = self.index.locate(campaign_id)
        result = mounted_action(
            self.livewire,
            page,
            page.component(key="tableSearch"),
            "duplicate",
            record=str(campaign_id),
        )
        return self._created(result)

    def set_running(
        self, campaign_id: int, running: bool, confirm: bool = False
    ) -> dict[str, object]:
        require_confirmation(confirm)
        section_path(campaign_id, "finish")
        page, before = self.index.locate(campaign_id)
        desired = "Running" if running else "Paused"
        if before["status"] == desired:
            return before
        if before["status"] not in ("Running", "Paused"):
            raise OutcraftError(
                "Campaign is not in a known running/paused state. Inspect setup first."
            )
        mounted_action(
            self.livewire,
            page,
            page.component(key="tableSearch"),
            "toggle_campaign",
            record=str(campaign_id),
        )
        result = self.index.locate(campaign_id)[1]
        if result["status"] != desired:
            raise OutcraftError("Campaign status did not reach the requested state.")
        return result

    def update(
        self, campaign_id: int, section: str, changes: dict[str, object], confirm: bool = False
    ) -> dict[str, object]:
        require_confirmation(confirm)
        path = section_path(campaign_id, section)
        page = self.livewire.page(path)
        component = form_component(page)
        data = object_dict(component.data["data"])
        if not changes:
            raise OutcraftError("Provide at least 1 changed field.")
        for key in changes:
            lookup(data, key)
        self.livewire.call(
            page,
            component,
            "__dispatch",
            params=["move-to-next-step", {}],
            updates={f"data.{k}": v for k, v in changes.items()},
        )
        fresh = form_component(self.livewire.page(path))
        actual = object_dict(fresh.data["data"])
        mismatches = [k for k, v in changes.items() if lookup(actual, k) != v]
        if mismatches:
            raise OutcraftError(
                "Saved values differ for: " + ", ".join(mismatches) + ". Inspect before retrying."
            )
        return {
            "id": campaign_id,
            "section": section,
            "saved": True,
            "published": False,
            "data": {k: lookup(actual, k) for k in changes},
        }

    def publish(self, campaign_id: int, confirm: bool = False) -> dict[str, object]:
        require_confirmation(confirm)
        page = self.livewire.page(section_path(campaign_id, "finish"))
        component = page.component("onboarding-topbar-actions")
        result = self.livewire.call(page, component, "publishKnowledgebaseDraft")
        return {
            "id": campaign_id,
            "publication": "submitted",
            "verified": False,
            "note": "Publication response received; inspect the dashboard diff and campaign status.",
            "events": result.effects.get("dispatches", []),
        }

    def action(
        self,
        campaign_id: int,
        section: str,
        action: str,
        *,
        record: str | None = None,
        data: dict[str, object] | None = None,
        arguments: dict[str, object] | None = None,
        context: dict[str, object] | None = None,
        component_name: str | None = None,
        inspect: bool = False,
        confirm: bool = False,
    ) -> dict[str, object]:
        require_confirmation(confirm)
        page = self.livewire.page(section_path(campaign_id, section))
        component = find_action_component(page, action, component_name)
        result = mounted_action(
            self.livewire, page, component, action, data, record, arguments, context, inspect
        )
        return {
            "component": describe(result),
            "submitted": not inspect,
            "verified": False,
            "note": "Re-open the section to verify persisted state.",
        }
