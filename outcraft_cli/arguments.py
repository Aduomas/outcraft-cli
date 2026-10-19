from __future__ import annotations

import argparse
from pathlib import Path

from .views import SECTIONS


def _write_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--yes", action="store_true", help="Confirm this mutation")
    parser.add_argument("--dry-run", action="store_true", help="Print a plan without HTTP or login")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Outcraft campaign CLI. Playwright login, HTTPX requests, JSON output."
    )
    add_connection_options(root)
    commands = root.add_subparsers(dest="group", required=True)
    auth = commands.add_parser("auth").add_subparsers(dest="command", required=True)
    for name in ("login", "status", "logout"):
        auth.add_parser(name)
    campaigns = commands.add_parser("campaigns").add_subparsers(dest="command", required=True)
    campaigns.add_parser("sections")
    listing = campaigns.add_parser("list", help="List 1 dashboard page; --page selects another")
    listing.add_argument("--archived", action="store_true")
    listing.add_argument("--page", type=int, default=1)
    listing.add_argument("--search", default="")
    show = campaigns.add_parser("show")
    show.add_argument("id", type=int)
    show.add_argument("--section", choices=SECTIONS, default="instructions")
    export = campaigns.add_parser("export")
    export.add_argument("id", type=int)
    create = campaigns.add_parser("create")
    create.add_argument("name")
    _write_options(create)
    rename = campaigns.add_parser("rename")
    rename.add_argument("id", type=int)
    rename.add_argument("name")
    _write_options(rename)
    for name in ("pause", "resume", "archive", "delete", "duplicate", "publish"):
        command = campaigns.add_parser(
            name, help="Archive, not permanent deletion" if name == "delete" else None
        )
        command.add_argument("id", type=int)
        _write_options(command)
    update = campaigns.add_parser("update")
    update.add_argument("id", type=int)
    update.add_argument("section", choices=SECTIONS)
    update.add_argument(
        "--data", required=True, help="JSON patch or @patch.json, without data. prefix"
    )
    _write_options(update)
    action = campaigns.add_parser("action", help="Run a discovered Filament action")
    action.add_argument("id", type=int)
    action.add_argument("section", choices=SECTIONS)
    action.add_argument("action")
    action.add_argument("--record")
    action.add_argument("--component")
    action.add_argument("--data", help="JSON form data or @file.json")
    action.add_argument("--arguments", help="JSON mount arguments")
    action.add_argument("--context", help="JSON schema/table action context")
    action.add_argument(
        "--inspect", action="store_true", help="Mount and return the form, do not submit it"
    )
    _write_options(action)
    sequence = commands.add_parser("sequence").add_subparsers(dest="command", required=True)
    for name in ("list", "show", "add", "edit", "delete"):
        command = sequence.add_parser(name)
        command.add_argument("id", type=int, help="Campaign ID")
        if name in ("show", "edit", "delete"):
            command.add_argument("record", help="Sequence step record key from sequence list")
        if name in ("add", "edit"):
            command.add_argument("--data", required=True, help="JSON object or @file.json")
        if name != "list":
            _write_options(command)
    inspect = commands.add_parser(
        "inspect", help="Inspect an authenticated same-origin dashboard path"
    )
    inspect.add_argument("path")
    call = commands.add_parser("call", help="Low-level Livewire method; always requires --yes")
    call.add_argument("path")
    call.add_argument("component")
    call.add_argument("method")
    call.add_argument("--params", default="[]", help="JSON array or @file.json")
    call.add_argument("--updates", default="{}", help="JSON updates or @file.json")
    _write_options(call)
    return root


def add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file", type=Path, help="Private file with OUTCRAFT_EMAIL and OUTCRAFT_PASSWORD"
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        default=Path.home() / ".local/state/outcraft-cli/session.json",
    )
    parser.add_argument("--base-url", default="https://app.outcraft.ai")
