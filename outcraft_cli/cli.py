from __future__ import annotations

import json
import sys

from .arguments import parser
from .client import Client
from .commands import mutation_plan, run
from .config import credentials, redact
from .errors import OutcraftError
from .transport import Transport
from .views import SECTIONS, require_confirmation


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        plan = mutation_plan(args)
        if plan is not None:
            if args.dry_run:
                print(json.dumps(redact(plan), indent=2, ensure_ascii=False))
                return 0
            require_confirmation(args.yes)
        if args.group == "campaigns" and args.command == "sections":
            print(json.dumps(SECTIONS, indent=2))
            return 0
        transport = Transport(args.base_url, args.session_file)
        client = Client(transport)
        if args.group == "auth":
            if args.command == "logout":
                if args.session_file.exists():
                    args.session_file.unlink()
                result: object = {"authenticated": False, "scope": "local session only"}
            elif args.command == "login":
                result = client.login(*credentials(args.env_file))
            else:
                client._campaign_page()
                result = {"authenticated": True, "origin": transport.base_url}
        else:
            client.ensure_authenticated(*credentials(args.env_file))
            result = run(args, client)
        print(json.dumps(redact(result), indent=2, ensure_ascii=False))
        return 0
    except (OutcraftError, OSError) as exc:
        message = (
            str(exc)
            if isinstance(exc, OutcraftError)
            else "Cannot access a local input or session file."
        )
        print(json.dumps({"error": message}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            json.dumps({"error": "Interrupted. Verify state before retrying a write."}),
            file=sys.stderr,
        )
        return 130
