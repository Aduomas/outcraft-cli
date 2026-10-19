from __future__ import annotations

import argparse
from typing import cast

from .client import Client
from .config import json_input
from .errors import OutcraftError
from .html import object_dict
from .views import SECTIONS, describe, section_path, valid_name

WRITES = {
    "create",
    "rename",
    "pause",
    "resume",
    "archive",
    "delete",
    "duplicate",
    "publish",
    "update",
    "action",
    "add",
    "edit",
    "show",
}


def mutation_plan(args: argparse.Namespace) -> dict[str, object] | None:
    write = args.group == "call" or (
        args.group in ("campaigns", "sequence") and args.command in WRITES
    )
    if args.group == "campaigns" and args.command == "show":
        write = False
    if not write:
        return None
    if hasattr(args, "name"):
        valid_name(args.name)
    if hasattr(args, "id"):
        section_path(args.id, getattr(args, "section", "finish"))
    operation = (
        "archive"
        if getattr(args, "command", "") == "delete" and args.group == "campaigns"
        else getattr(args, "command", "call")
    )
    plan: dict[str, object] = {
        "operation": operation,
        "group": args.group,
        "id": getattr(args, "id", None),
        "dry_run": bool(args.dry_run),
    }
    for key in ("name", "section", "record", "path", "component", "method", "action", "inspect"):
        if hasattr(args, key):
            plan[key] = getattr(args, key)
    for key in ("data", "arguments", "context", "updates", "params"):
        if hasattr(args, key) and getattr(args, key):
            plan[key] = json_input(getattr(args, key), object_only=key != "params")
    if operation == "archive":
        plan["permanently_deleted"] = False
    return plan


def run(args: argparse.Namespace, client: Client) -> object:
    if args.group == "inspect":
        return client.inspect_path(args.path)
    if args.group == "call":
        page = client.livewire.page(args.path)
        params = json_input(args.params, object_only=False)
        if not isinstance(params, list):
            raise OutcraftError("--params must be a JSON array.")
        result = client.livewire.call(
            page,
            page.component(args.component),
            args.method,
            params=cast(list[object], params),
            updates=object_dict(json_input(args.updates)),
        )
        return {
            "component": describe(result),
            "verified": False,
            "note": "Low-level response only; inspect persisted state before retrying.",
        }
    command = args.command
    if args.group == "sequence":
        if command == "list":
            return client.inspect(args.id, "sequence")
        return client.action(
            args.id,
            "sequence",
            {"show": "edit", "add": "addStep"}.get(command, command),
            record=getattr(args, "record", None),
            data=object_dict(json_input(getattr(args, "data", None))),
            inspect=command == "show",
            confirm=args.yes,
        )
    if command == "sections":
        return SECTIONS
    if command == "list":
        records = client.campaigns(args.archived, args.page)
        return [r for r in records if args.search.casefold() in str(r["name"]).casefold()]
    if command == "show":
        return client.inspect(args.id, args.section)
    if command == "export":
        return client.export(args.id)
    if command == "create":
        return client.create(args.name, confirm=args.yes)
    if command == "rename":
        return client.rename(args.id, args.name, confirm=args.yes)
    if command == "duplicate":
        return client.duplicate(args.id, confirm=args.yes)
    if command in ("archive", "delete"):
        return client.archive(args.id, confirm=args.yes)
    if command in ("pause", "resume"):
        return client.set_running(args.id, command == "resume", confirm=args.yes)
    if command == "update":
        return client.update(
            args.id, args.section, object_dict(json_input(args.data)), confirm=args.yes
        )
    if command == "publish":
        return client.publish(args.id, confirm=args.yes)
    if command == "action":
        return client.action(
            args.id,
            args.section,
            args.action,
            record=args.record,
            data=object_dict(json_input(args.data)),
            arguments=object_dict(json_input(args.arguments)),
            context=object_dict(json_input(args.context)),
            component_name=args.component,
            inspect=args.inspect,
            confirm=args.yes,
        )
    raise OutcraftError("Unknown command.")
