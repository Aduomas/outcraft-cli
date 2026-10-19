from __future__ import annotations

import re
from urllib.parse import urlsplit

from .actions import find_action_component, mounted_action
from .errors import OutcraftError
from .html import Component, Page, object_dict
from .livewire import Livewire
from .transport import Transport
from .views import (
    SECTIONS,
    campaigns_from_page,
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

    def login(self, email: str, password: str) -> dict[str, object]:
        if not email or not password:
            raise OutcraftError(
                "Set OUTCRAFT_EMAIL and OUTCRAFT_PASSWORD locally, then run auth login."
            )
        page = self.livewire.page("/auth/login")
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
        self._campaign_page()
        return {"authenticated": True, "origin": self.transport.base_url}

    def _campaign_page(self, archived: bool = False, page: int = 1) -> Page:
        if page < 1:
            raise OutcraftError("Page must be positive.")
        path = "/campaigns/archived" if archived else "/campaigns"
        result = self.livewire.page(path + (f"?page={page}" if page > 1 else ""))
        if "/auth/" in urlsplit(result.url).path or any(
            "password" in c.data for c in result.components
        ):
            raise OutcraftError("Not logged in. Run outcraft --env-file .env auth login.")
        if not result.components:
            raise OutcraftError("Campaign dashboard components are missing.")
        return result

    def ensure_authenticated(self, email: str = "", password: str = "") -> None:
        page = self.livewire.page("/campaigns")
        if "/auth/" in urlsplit(page.url).path or any(
            "password" in c.data for c in page.components
        ):
            self.login(email, password)

    def campaigns(self, archived: bool = False, page: int = 1) -> list[dict[str, object]]:
        return campaigns_from_page(self._campaign_page(archived, page))

    def _record(self, campaign_id: int, *, archived: bool = False) -> dict[str, object]:
        matches = [c for c in self.campaigns(archived) if c["id"] == campaign_id]
        if len(matches) != 1:
            raise OutcraftError(
                "Campaign not found on this list page. Use inspect to check pagination."
            )
        return matches[0]

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
        valid_name(name)
        page = self._campaign_page()
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
        records = self.campaigns()
        candidates = [c for c in records if match and c["id"] == int(match[1])]
        if len(candidates) != 1 or (name is not None and candidates[0]["name"] != name):
            raise OutcraftError(
                "Write submitted, but new campaign identity was not verified. Inspect before retrying."
            )
        return candidates[0]

    def rename(self, campaign_id: int, name: str, confirm: bool = False) -> dict[str, object]:
        require_confirmation(confirm)
        valid_name(name)
        section_path(campaign_id, "finish")
        page = self._campaign_page()
        mounted_action(
            self.livewire,
            page,
            page.component(key="tableSearch"),
            "edit_name",
            {"name": name},
            str(campaign_id),
        )
        result = self._record(campaign_id)
        if result["name"] != name:
            raise OutcraftError("Rename did not persist. Inspect the campaign before retrying.")
        return result

    def archive(self, campaign_id: int, confirm: bool = False) -> dict[str, object]:
        require_confirmation(confirm)
        section_path(campaign_id, "finish")
        page = self._campaign_page()
        mounted_action(
            self.livewire,
            page,
            page.component(key="tableSearch"),
            "archive",
            record=str(campaign_id),
        )
        if any(c["id"] == campaign_id for c in self.campaigns()):
            raise OutcraftError("Campaign is still in the active list; archive was not verified.")
        record = self._record(campaign_id, archived=True)
        return {**record, "operation": "archive", "permanently_deleted": False}

    def duplicate(self, campaign_id: int, confirm: bool = False) -> dict[str, object]:
        require_confirmation(confirm)
        section_path(campaign_id, "finish")
        page = self._campaign_page()
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
        before = self._record(campaign_id)
        desired = "Running" if running else "Paused"
        if before["status"] == desired:
            return before
        if before["status"] not in ("Running", "Paused"):
            raise OutcraftError(
                "Campaign is not in a known running/paused state. Inspect setup first."
            )
        page = self._campaign_page()
        mounted_action(
            self.livewire,
            page,
            page.component(key="tableSearch"),
            "toggle_campaign",
            record=str(campaign_id),
        )
        result = self._record(campaign_id)
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
