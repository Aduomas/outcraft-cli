from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlsplit

from .errors import OutcraftError
from .html import Page
from .livewire import Livewire
from .views import campaigns_from_page


class CampaignIndex:
    def __init__(self, livewire: Livewire) -> None:
        self.livewire = livewire

    def page(self, archived: bool = False, page: int = 1) -> Page:
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

    def items(self, archived: bool = False, page: int = 1) -> list[dict[str, object]]:
        return campaigns_from_page(self.page(archived, page))

    def pages(
        self, archived: bool = False, first: Page | None = None
    ) -> Iterator[tuple[Page, list[dict[str, object]]]]:
        seen: set[tuple[int, ...]] = set()
        for number in range(1, 101):
            page = first if number == 1 and first is not None else self.page(archived, number)
            records = campaigns_from_page(page)
            signature = tuple(int(str(r["id"])) for r in records)
            if signature in seen:
                raise OutcraftError(
                    "Campaign pagination repeated; cannot safely verify the target."
                )
            seen.add(signature)
            yield page, records
            if not records or not has_next_page(page):
                return
        raise OutcraftError(
            "Campaign pagination exceeded 100 pages; narrow the account before editing."
        )

    def locate(
        self, campaign_id: int, archived: bool = False, first: Page | None = None
    ) -> tuple[Page, dict[str, object]]:
        for page, records in self.pages(archived, first):
            for record in records:
                if record["id"] == campaign_id:
                    return page, record
        raise OutcraftError("Campaign not found in the requested dashboard list.")

    def contains(self, campaign_id: int, archived: bool = False) -> bool:
        return any(r["id"] == campaign_id for _, records in self.pages(archived) for r in records)


def has_next_page(page: Page) -> bool:
    for node in page.root.find():
        if (
            node.tag not in ("button", "a")
            or "disabled" in node.attrs
            or node.attrs.get("aria-disabled") == "true"
        ):
            continue
        if node.attrs.get("rel") == "next" or any(
            "nextPage(" in v
            for k, v in node.attrs.items()
            if k.startswith(("wire:click", "x-on:click"))
        ):
            return True
    return False
