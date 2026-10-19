from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .errors import OutcraftError
from .html import object_dict


def credentials(env_file: Path | None) -> tuple[str, str]:
    values: dict[str, str] = {}
    if env_file:
        if env_file.stat().st_mode & 0o077:
            raise OutcraftError("Credential file must be private. Run chmod 600 on your env file.")
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.removeprefix("export ").partition("=")
            if not separator:
                raise OutcraftError(
                    "Invalid env file: use KEY=value lines, without shell commands."
                )
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[key] = value
    return (
        os.environ.get("OUTCRAFT_EMAIL", values.get("OUTCRAFT_EMAIL", "")),
        os.environ.get("OUTCRAFT_PASSWORD", values.get("OUTCRAFT_PASSWORD", "")),
    )


def json_input(value: str | None, *, object_only: bool = True) -> object:
    if value is None:
        return {} if object_only else []
    try:
        text = Path(value[1:]).read_text() if value.startswith("@") else value
        result: object = json.loads(text)
    except (OSError, ValueError):
        raise OutcraftError("Expected JSON, or @/path/to/file.json containing JSON.") from None
    return object_dict(result) if object_only else result


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: "[redacted]"
            if re.search(
                r"password|token|secret|cookie|authorization|csrf|snapshot|checksum|api[_-]?key|private[_-]?key",
                str(k),
                re.I,
            )
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(/api/v\d+/trigger/)[^\s\"<>]+", r"\1[redacted]", value)
    return value
