from __future__ import annotations

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import cast

from .errors import OutcraftError


def object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise OutcraftError("Expected a JSON object from Outcraft.")
    return cast(dict[str, object], value)


def unwrap(value: object) -> object:
    if isinstance(value, list):
        if len(value) == 2 and isinstance(value[1], dict) and isinstance(value[1].get("s"), str):
            return unwrap(value[0])
        return [unwrap(v) for v in value]
    if isinstance(value, dict):
        return {k: unwrap(v) for k, v in value.items()}
    return value


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(
            " ".join(c.text if isinstance(c, Node) else c for c in self.children).split()
        )

    def find(self, tag: str | None = None) -> list[Node]:
        result = [self] if tag is None or self.tag == tag else []
        for child in self.children:
            if isinstance(child, Node):
                result.extend(child.find(tag))
        return result

    def owned(self, tag: str | None = None) -> list[Node]:
        result = [self] if tag is None or self.tag == tag else []
        for child in self.children:
            if isinstance(child, Node) and "wire:id" not in child.attrs:
                result.extend(child.owned(tag))
        return result


@dataclass
class Component:
    snapshot: str
    effects: dict[str, object] = field(default_factory=dict)
    root: Node = field(default_factory=lambda: Node("fragment"))

    def __post_init__(self) -> None:
        try:
            raw = object_dict(json.loads(self.snapshot))
            self.memo = object_dict(raw["memo"])
            self.name = str(self.memo["name"])
            data = unwrap(raw["data"])
            self.data = object_dict({} if data == [] else data)
        except (ValueError, KeyError, TypeError) as exc:
            raise OutcraftError(
                "Invalid Livewire snapshot; the dashboard protocol may have changed."
            ) from exc

    @property
    def actions(self) -> list[dict[str, str]]:
        result = []
        for node in self.root.owned():
            for key, value in node.attrs.items():
                if key.startswith(("wire:click", "wire:submit")):
                    result.append({"label": node.text[:120], "expression": value})
        return result

    @property
    def fields(self) -> list[dict[str, str]]:
        fields = []
        for node in self.root.owned():
            for key, value in node.attrs.items():
                if key.startswith("wire:model"):
                    item: dict[str, str] = {"path": value, "type": node.attrs.get("type", node.tag)}
                    item.update(
                        {
                            k: node.attrs[k]
                            for k in ("id", "min", "max", "required")
                            if k in node.attrs
                        }
                    )
                    if node.attrs.get("type") == "radio" and "value" in node.attrs:
                        item["value"] = node.attrs["value"]
                    label = next(
                        (
                            label
                            for label in self.root.owned("label")
                            if label.attrs.get("for") == node.attrs.get("id") and "id" in node.attrs
                        ),
                        None,
                    )
                    if label is not None:
                        item["label"] = label.text
                    fields.append(item)
        return fields


@dataclass
class Page:
    csrf: str = ""
    update_uri: str = ""
    components: list[Component] = field(default_factory=list)
    root: Node = field(default_factory=lambda: Node("document"))
    url: str = ""

    def component(self, name: str | None = None, *, key: str | None = None) -> Component:
        matches = [
            c
            for c in self.components
            if (name is None or c.name == name) and (key is None or key in c.data)
        ]
        if len(matches) != 1:
            raise OutcraftError("Select exactly 1 component by its current name from inspect.")
        return matches[0]


VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self.stack = [self.page.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {k: v or "" for k, v in attrs}
        node = Node(tag, attributes)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)
        if tag == "meta" and attributes.get("name") == "csrf-token":
            self.page.csrf = attributes.get("content", "")
        if tag == "script":
            self.page.csrf = attributes.get("data-csrf", self.page.csrf)
            self.page.update_uri = attributes.get("data-update-uri", self.page.update_uri)
        if "wire:snapshot" in attributes:
            try:
                effects = object_dict(json.loads(attributes.get("wire:effects", "{}")))
                self.page.components.append(Component(attributes["wire:snapshot"], effects, node))
            except (ValueError, TypeError) as exc:
                raise OutcraftError("Invalid Livewire component markup.") from exc

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_page(html: str) -> Page:
    parser = _Parser()
    parser.feed(html)
    parser.close()
    return parser.page
