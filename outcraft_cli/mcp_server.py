from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from .arguments import add_connection_options
from .client import Client
from .config import credentials, redact
from .errors import OutcraftError
from .transport import Transport
from .views import SECTIONS

if TYPE_CHECKING:
    from mcp.server import MCPServer


def create_server(
    base_url: str, session_file: Path, env_file: Path | None = None
) -> MCPServer[object]:
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    server: MCPServer[object] = MCPServer(
        "Outcraft",
        version="0.1.0",
        log_level="WARNING",
        instructions="Unofficial Outcraft campaign tools. Writes require explicit user intent and confirm=true. "
        "Archive is not permanent deletion. Inspect persisted state after unverified actions. "
        "Never retry a failed mutation automatically; resume can contact leads.",
    )
    lock = Lock()
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
    write = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

    def execute(operation: Callable[[Client], object], confirm: bool | None = None) -> str:
        try:
            if confirm is False:
                raise OutcraftError(
                    "Mutation refused. Set confirm=true only with explicit user intent."
                )
            with lock, Transport(base_url, session_file) as transport:
                client = Client(transport)
                client.ensure_authenticated(*credentials(env_file))
                return json.dumps(redact(operation(client)), ensure_ascii=False)
        except OutcraftError as exc:
            raise ToolError(str(exc)) from None
        except OSError:
            raise ToolError("Cannot access a local input or session file.") from None

    @server.tool(annotations=read, structured_output=False)
    def campaign_sections() -> str:
        """List dashboard section names and paths without login or network access."""
        return json.dumps(SECTIONS)

    @server.tool(annotations=read, structured_output=False)
    def list_campaigns(archived: bool = False, page: int = 1, search: str = "") -> str:
        """Retrieve 1 campaign page, optionally archived or filtered by name."""
        return execute(lambda client: client.campaigns(archived, page, search))

    @server.tool(annotations=read, structured_output=False)
    def get_campaign(campaign_id: int, section: str = "instructions") -> str:
        """Inspect a campaign section's current fields, forms and available actions."""
        return execute(lambda client: client.inspect(campaign_id, section))

    @server.tool(annotations=read, structured_output=False)
    def export_campaign(campaign_id: int) -> str:
        """Retrieve the campaign's available setup sections."""
        return execute(lambda client: client.export(campaign_id))

    @server.tool(annotations=read, structured_output=False)
    def inspect_dashboard(path: str) -> str:
        """Inspect an authenticated dashboard path on the configured Outcraft origin."""
        return execute(lambda client: client.inspect_path(path))

    @server.tool(annotations=write, structured_output=False)
    def create_campaign(name: str, confirm: bool = False) -> str:
        """Create an empty campaign, without leads or launch, and verify its name."""
        return execute(lambda client: client.create(name, confirm=True), confirm)

    @server.tool(annotations=write, structured_output=False)
    def rename_campaign(campaign_id: int, name: str, confirm: bool = False) -> str:
        """Rename the exact campaign and verify the saved name."""
        return execute(lambda client: client.rename(campaign_id, name, confirm=True), confirm)

    @server.tool(annotations=write, structured_output=False)
    def update_campaign(
        campaign_id: int, section: str, changes: dict[str, object], confirm: bool = False
    ) -> str:
        """Patch existing form fields, using dot paths without data. prefix; verify the saved draft."""
        return execute(
            lambda client: client.update(campaign_id, section, changes, confirm=True), confirm
        )

    @server.tool(annotations=write, structured_output=False)
    def set_campaign_running(campaign_id: int, running: bool, confirm: bool = False) -> str:
        """Pause or resume a configured campaign; resuming can contact real leads."""
        return execute(
            lambda client: client.set_running(campaign_id, running, confirm=True), confirm
        )

    @server.tool(annotations=write, structured_output=False)
    def archive_campaign(campaign_id: int, confirm: bool = False) -> str:
        """Archive a campaign, not permanent deletion, and verify its archived state."""
        return execute(lambda client: client.archive(campaign_id, confirm=True), confirm)

    @server.tool(annotations=write, structured_output=False)
    def duplicate_campaign(campaign_id: int, confirm: bool = False) -> str:
        """Duplicate a campaign and verify the new campaign ID."""
        return execute(lambda client: client.duplicate(campaign_id, confirm=True), confirm)

    @server.tool(annotations=write, structured_output=False)
    def publish_campaign(campaign_id: int, confirm: bool = False) -> str:
        """Submit campaign draft publication; a submitted response is not verified publication."""
        return execute(lambda client: client.publish(campaign_id, confirm=True), confirm)

    @server.tool(annotations=write, structured_output=False)
    def campaign_action(
        campaign_id: int,
        section: str,
        action: str,
        record: str | None = None,
        data: dict[str, object] | None = None,
        arguments: dict[str, object] | None = None,
        context: dict[str, object] | None = None,
        component_name: str | None = None,
        inspect: bool = False,
        confirm: bool = False,
    ) -> str:
        """Run a currently discovered action, including sequence add/edit/delete; inspect mounts without submitting."""
        return execute(
            lambda client: client.action(
                campaign_id,
                section,
                action,
                record=record,
                data=data,
                arguments=arguments,
                context=context,
                component_name=component_name,
                inspect=inspect,
                confirm=True,
            ),
            confirm,
        )

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unofficial Outcraft MCP server over stdio.")
    add_connection_options(parser)
    args = parser.parse_args(argv)
    try:
        create_server(args.base_url, args.session_file, args.env_file).run(transport="stdio")
    except ImportError:
        print("MCP support needs the mcp extra: install outcraft-cli[mcp].", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
