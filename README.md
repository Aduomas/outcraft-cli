# Outcraft

Unofficial Outcraft CLI and MCP server for agents to read and manage campaigns. Not affiliated with Outcraft; dashboard changes can break this client.

## Install

Requires Python 3.11+. Install from this checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[browser,mcp]'
python -m playwright install --with-deps --only-shell chromium
```

Playwright launches a temporary browser for email/password login, then closes it. HTTPX handles every campaign request. No personal Chrome profile or stealth browser is used. Linux requires Chromium's system libraries.

## CLI

Set `OUTCRAFT_EMAIL` and `OUTCRAFT_PASSWORD` in your environment or a private `.env` file (`chmod 600 .env`). Google sign-in, MFA and CAPTCHA solving are not automated.

```bash
outcraft --env-file .env auth login
outcraft campaigns list
outcraft campaigns show 123 --section sequence
outcraft campaigns rename 123 'New campaign name' --yes
outcraft --help
```

Sessions are saved locally with owner-only permissions. `outcraft auth logout` removes the local session. Writes need `--yes`; `--dry-run` makes no requests. Campaign `delete` means archive, not permanent deletion.

## MCP

Add this to your agent's MCP configuration, using absolute executable and env-file paths:

```json
{"mcpServers":{"outcraft":{"command":"/absolute/path/outcraft-cli/.venv/bin/outcraft-mcp","args":["--env-file","/absolute/path/.env"]}}}
```

The stdio server shares the CLI client and supports campaign reads, creation, editing, archive, duplication, pause/resume, publication and discovered dashboard actions. Mutation tools require `confirm: true`. It opens no listening port and logs in only when needed.

## Verification

Browser-session handoff to HTTPX is verified against Outcraft's login validation endpoint. Real email/password login and authenticated campaign writes still need live verification; no credentials have been supplied. A successful submission is not a claim that a campaign was published or launched.
