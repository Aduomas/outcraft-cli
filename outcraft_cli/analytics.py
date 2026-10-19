from __future__ import annotations

import re

from .errors import OutcraftError
from .html import Component, Node, Page, parse_page
from .leads import fragment_name
from .livewire import Livewire

RANGES = ("24h", "7d", "14d", "30d", "360d")
LAZY = re.compile(r"lazyLoad\('([^']+)'\)")
PERCENT = re.compile(r"\(([\d.]+)%\)")


class Analytics:
    def __init__(self, livewire: Livewire) -> None:
        self.livewire = livewire

    def overview(self, campaign_id: int | None = None, period: str = "7d") -> dict[str, object]:
        if period not in RANGES:
            raise OutcraftError("Use a range of " + ", ".join(RANGES))
        if campaign_id is not None and campaign_id < 1:
            raise OutcraftError("Use a positive campaign ID.")
        page = self.livewire.page(
            f"/campaigns/{campaign_id}/analytics" if campaign_id else "/analytics"
        )
        stats, root = stat_components(page.components), page.root
        if period != "7d":
            filters = next(
                (c for c in page.components if str(fragment_name(c)).startswith("analytics.")), None
            )
            if filters is None:
                raise OutcraftError("Analytics filter markup changed; cannot change the range.")
            html = str(
                self.livewire.call(
                    page, filters, "$refresh", updates={"range": period}
                ).effects.get("html", "")
            )
            rendered = parse_page(html)
            stats, root = stat_components(rendered.components), rendered.root
        if not stats:
            raise OutcraftError("Analytics markup changed; no stat blocks found.")
        return {
            "campaign_id": campaign_id,
            "range": period,
            "blocks": [self._load(page, component, root) for component in stats],
        }

    def _load(self, page: Page, component: Component, root: Node) -> dict[str, object]:
        token = lazy_token(root, component)
        loaded = self.livewire.call(page, component, "__lazyLoad", params=[token])
        return parse_block(loaded.root)


def stat_components(components: list[Component]) -> list[Component]:
    return [c for c in components if c.name.startswith("v2.analytics.")]


def lazy_token(root: Node, component: Component) -> str:
    for node in root.find():
        if node.attrs.get("wire:id") != component.memo.get("id"):
            continue
        for value in node.attrs.values():
            match = LAZY.search(value)
            if match:
                return match[1]
    raise OutcraftError("Analytics block has no lazy-load token; the dashboard may have changed.")


def parse_block(root: Node) -> dict[str, object]:
    """A block is an h3 title, then label spans each followed by a large value div."""
    nodes = root.find()
    title = next((n.text for n in nodes if n.tag == "h3"), "")
    metrics: dict[str, dict[str, float]] = {}
    label: str | None = None
    for node in nodes:
        if node.tag == "span" and node.text and not node.find("div"):
            label = node.text
        elif label and node.tag == "div" and "text-3xl" in node.attrs.get("class", ""):
            match = PERCENT.search(label)
            number = float(node.text.replace("$", "").replace(",", "") or 0)
            entry: dict[str, float] = {"value": int(number) if number.is_integer() else number}
            if match:
                entry["percent"] = float(match[1])
            name = PERCENT.sub("", label).strip()
            metrics[re.sub(r"\W+", "_", name.lower()).strip("_")] = entry
            label = None
    return {"title": title, "metrics": metrics}
