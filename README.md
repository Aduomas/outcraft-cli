# outcraft-cli

Manage Outcraft campaigns from the terminal or Python instead of its dashboard. This standalone client uses the same login and Livewire requests as the dashboard, with 0 runtime dependencies and JSON output.

**Blocked for direct writes:** authenticated GETs work with an existing session, but live Livewire POSTs return Cloudflare HTTP 403, including mounting a rename form. This is a draft implementation, not a working replacement for campaign editing until Outcraft permits this client or exposes supported campaign-management endpoints.

## Run

Python 3.11 or newer is sufficient, with no install required. While PR #1 is draft, use its feature branch:

```bash
git clone --branch feat/campaign-cli git@github.com:Aduomas/outcraft-cli.git
cd outcraft-cli
./outcraft --help
cp .env.example .env
chmod 600 .env
# Fill OUTCRAFT_EMAIL and OUTCRAFT_PASSWORD locally.
./outcraft --env-file .env auth login
./outcraft campaigns list
```

Passwords containing `$`, `#`, or backslashes are treated literally, never executed or expanded. Nonempty environment variables take precedence over the explicitly selected env file.

Alternatively, install the command into an isolated environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/outcraft --help
```

## Campaigns

```bash
./outcraft campaigns list
./outcraft campaigns list --archived
./outcraft campaigns list --page 2 --search 'Delivery'
./outcraft campaigns sections
./outcraft campaigns show 1438 --section instructions
./outcraft campaigns export 1438 > campaign.json

./outcraft campaigns create 'New outreach' --dry-run
./outcraft campaigns create 'New outreach' --yes
./outcraft campaigns rename 1438 'Renewal feedback' --yes
./outcraft campaigns pause 1438 --yes
./outcraft campaigns resume 1438 --yes
./outcraft campaigns duplicate 1438 --yes
./outcraft campaigns archive 1438 --yes
```

List returns **1 dashboard page**, not an invented all-records API. `--search` filters that page locally. Export retrieves all 13 setup sections for 1 campaign, including the current editable data, table rows, actions, field bindings, and links.

Create and rename normalize surrounding and repeated whitespace in campaign names; a dry run shows the normalized name.

**`campaigns delete` means archive**, explicitly reported as `permanently_deleted: false`. No permanent-delete contract was visible, so the CLI does not invent one. The archive view exposes Re-Activate through the discovered `unarchive` action, not permanent deletion. Resume can contact real leads; it always requires `--yes`.

## Edit setup

Read the section first, then send only the changed fields:

```bash
./outcraft campaigns show 1438 --section general > general.json
./outcraft campaigns update 1438 general \
  --data '{"should_shorten_links":false}' --dry-run
./outcraft campaigns update 1438 general \
  --data '{"should_shorten_links":false}' --yes
./outcraft campaigns update 1438 instructions --data @instructions-patch.json --yes
./outcraft campaigns publish 1438 --yes
```

Patch keys are relative to `data`, for example `should_shorten_links`, not `data.should_shorten_links`. Nested fields use dot paths. Rich-text fields accept the document JSON returned by `show`, so structured variables and formatting need not be flattened into plain text.

Update validates field existence, sends only the patch through the observed Continue event, then re-fetches the section and compares persisted values. It **does not publish or start a campaign**. Publication is a separate command; its current result is explicitly `publication: submitted, verified: false`, not proof that a campaign launched.

The sections are `source`, `brand`, `instructions`, `general`, `schedule`, `channels`, `sequence`, `evaluations`, `conditions`, `agent`, `finish`, `countries`, and `overrides`. Table/action-driven sections use the action command rather than the form-update command.

## Sequences and other actions

```bash
./outcraft sequence list 1438
./outcraft sequence show 1438 11125 --yes
./outcraft sequence edit 1438 11125 \
  --data '{"relative_delay_minutes":1,"relative_delay_unit":"days"}' --yes
./outcraft sequence add 1438 \
  --data '{"available_flow_step_id":123,"relative_delay_minutes":24,"relative_delay_unit":"hours"}' --yes
./outcraft sequence delete 1438 11125 --dry-run
```

Use actual record keys and available-step IDs from the current account. The values above are examples, not reusable IDs. `sequence show` mounts the observed edit form without submitting; it still requires `--yes` because it makes a Livewire POST. Sequence deletion is a real step deletion, unlike campaign deletion's archive alias.

Every setup section exposes its current action expressions through `show`. The general action command supports create/edit/remove forms, argument payloads, and schema-specific contexts:

```bash
./outcraft campaigns action 1438 overrides create --inspect --yes
./outcraft campaigns action 1438 overrides create --data @override.json --yes
./outcraft campaigns action 1438 evaluations edit --record 2396 --inspect --yes
./outcraft campaigns action 1438 evaluations edit --record 2396 --data @evaluation.json --yes
```

`--inspect` mounts a form but does not submit it. It is **not** a dry run: a provider action can execute during mounting. These generic actions and sequence commands report `verified: false`; re-open the same record to check durable values before publishing or retrying.

## Low-level access

```bash
./outcraft inspect /leads
./outcraft inspect /campaigns
./outcraft call /campaigns '<component name from inspect>' '$refresh' --yes
./outcraft call /campaigns/1438/onboarding/campaign/sequence \
  '<component name from show>' reorderTable \
  --params '[["11126","11125","11132"],"11125"]' --dry-run
```

`call` accepts `--params` as a JSON array and `--updates` as a JSON object, including `@file.json`. It returns a decoded response, never a claim of persistence. Only the configured origin is allowed; redirects and update URIs cannot send credentials to another origin.

## Python API

```python
import os
from pathlib import Path
from outcraft_cli.client import Client
from outcraft_cli.transport import Transport

client = Client(Transport(session_file=Path.home() / ".local/state/outcraft-cli/session.cookies"))
client.login(os.environ["OUTCRAFT_EMAIL"], os.environ["OUTCRAFT_PASSWORD"])
campaigns = client.campaigns()
setup = client.inspect(campaigns[0]["id"], "general")
# Explicit confirmation is also required through the Python API.
client.rename(campaigns[0]["id"], "Renewal feedback", confirm=True)
```

`Client` is the importable API; there is no unauthenticated local HTTP server. The low-level `Livewire` and `Transport` objects deliberately expose raw requests, so callers using those are responsible for mutation authorization and verification.

## Sessions and failures

- **2 credential values:** `OUTCRAFT_EMAIL` and `OUTCRAFT_PASSWORD`, read only for login. The CLI does not write passwords to its session file.
- **1 private cookie file:** `~/.local/state/outcraft-cli/session.cookies`, written atomically with mode `0600`. `--session-file` selects another; do not share it across accounts or concurrent processes.
- **0 automatic write retries:** a timeout can mean a write succeeded. Inspect the target before retrying, especially create, duplicate, delete, or publish.
- **2 safeguards:** mutations require `--yes`; `--dry-run` makes no HTTP requests, including login. Output redacts credential/token fields and API trigger URL identifiers.
- **1 manual boundary:** CAPTCHA, 2FA, or an unsupported login change stops the client, rather than bypassing the provider's checks. `auth logout` deletes only the local cookie file, not the Chrome session.

## Development and evidence

```bash
python3 -m unittest discover -s tests -v
uvx --from ruff==0.17.1 ruff check .
uvx --from ruff==0.17.1 ruff format --check .
uvx --from mypy==2.4.0 mypy outcraft_cli
```

Tests use synthetic HTML and a real loopback HTTP server, not customer exports. They exercise login, cookie rotation, cross-origin refusal, exact signed-snapshot reuse, validation failures, create/rename persistence checks, partial-field updates, and network-free CLI dry runs.

[Live verification and the Cloudflare blocker](docs/verification.md) records what actually ran. [Protocol evidence and observed contracts](docs/protocol.md) records the live inspection, the 13 setup routes, and the save request intercepted before reaching Outcraft. The client successfully fetched the live unauthenticated login handshake and, using a private existing session, read the authenticated campaign list. Its direct POSTs were blocked by Cloudflare HTTP 403; no CAPTCHA, WAF, or bot-protection bypass is implemented.
