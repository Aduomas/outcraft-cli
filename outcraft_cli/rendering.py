from __future__ import annotations

from copy import deepcopy

from .html import Node, parse_page


def render_effects(root: Node, effects: dict[str, object], component_id: object) -> Node:
    html = effects.get("html")
    if isinstance(html, str):
        document = parse_page(html).root
        root = next(
            (n for n in document.find() if n.attrs.get("wire:id") == component_id), document
        )
    else:
        root = deepcopy(root)
    partials = effects.get("partials")
    if isinstance(partials, dict):
        for name, fragment in partials.items():
            if isinstance(fragment, str):
                children = parse_page(fragment).root.children
                if not replace_partial(root, name, children):
                    root.children.extend(children)
    return root


def replace_partial(root: Node, name: str, replacement: list[Node | str]) -> bool:
    for index, child in enumerate(root.children):
        if not isinstance(child, Node) or "wire:id" in child.attrs:
            continue
        if child.attrs.get("wire:partial") == name:
            root.children[index : index + 1] = replacement
            return True
        if replace_partial(child, name, replacement):
            return True
    return False
