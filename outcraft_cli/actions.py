from __future__ import annotations

import re

from .errors import OutcraftError
from .html import Component, Page, object_dict
from .livewire import Livewire


def find_action_component(page: Page, action: str, name: str | None = None) -> Component:
    pattern = re.compile(r"mountAction\(['\"]" + re.escape(action) + r"['\"]")
    matches = [
        c
        for c in page.components
        if (name is None or c.name == name)
        and any(pattern.search(a["expression"]) for a in c.actions)
    ]
    if len(matches) != 1:
        raise OutcraftError(
            "Action not exposed by exactly 1 current component. Use inspect to choose it."
        )
    return matches[0]


def mounted_action(
    livewire: Livewire,
    page: Page,
    component: Component,
    action: str,
    data: dict[str, object] | None = None,
    record: str | None = None,
    arguments: dict[str, object] | None = None,
    context: dict[str, object] | None = None,
    inspect: bool = False,
) -> Component:
    params: list[object] = [action]
    resolved_context = context or ({"recordKey": record, "table": True} if record else {})
    if arguments or resolved_context:
        params.extend([arguments or {}, resolved_context])
    mounted = livewire.call(page, component, "mountAction", params=params)
    actions = mounted.data.get("mountedActions")
    if not isinstance(actions, list) or not actions:
        if data:
            raise OutcraftError(
                "Action ran without a form; supplied data was not applied. Verify state."
            )
        return mounted
    if inspect:
        return mounted
    updates = {f"mountedActions.{len(actions) - 1}.data.{k}": v for k, v in (data or {}).items()}
    result = livewire.call(page, mounted, "callMountedAction", updates=updates)
    remaining = result.data.get("mountedActions")
    if remaining:
        raise OutcraftError(
            "Action is still mounted; validation or an additional confirmation may be required."
        )
    return result


def mounted_data(component: Component) -> dict[str, object]:
    actions = component.data.get("mountedActions")
    if not isinstance(actions, list) or not actions:
        raise OutcraftError("No mounted form returned by Outcraft.")
    return object_dict(object_dict(actions[-1]).get("data", {}))
