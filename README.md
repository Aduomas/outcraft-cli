# Outcraft

Unofficial Outcraft CLI and MCP server for agents to read and manage campaigns. Not affiliated with Outcraft; dashboard changes can break this client.

![One prompt to set up Outcraft CLI and MCP](assets/onboarding.svg)

## Set up with your agent

Copy this prompt into **Claude Code, Codex, or another agent with terminal access**:

```text
Set up https://github.com/Aduomas/outcraft-cli so I can manage Outcraft through this
agent using its CLI and MCP server.

1. Read the README. Safely clone or reuse this repo without overwriting existing work.
   Use an isolated Python 3.11+ environment, install the browser and MCP extras, and
   install Playwright Chromium's headless shell. Ask before privileged OS changes.
2. Keep OUTCRAFT_EMAIL and OUTCRAFT_PASSWORD in an ignored, owner-only .env file.
   Preserve existing values. Let me enter credentials privately or pause for me to fill
   the local file. Never ask for passwords in chat, print them, or put them in MCP
   settings.
3. Register the stdio MCP server in this agent using absolute executable and --env-file
   paths. Preserve other MCP entries. If this host cannot run local commands or stdio
   MCP, explain that limitation instead of claiming setup succeeded.
4. Verify CLI login, a read-only campaign list, and MCP tool discovery. If a restart or
   new session is needed, say exactly what remains unverified. Do not create, edit,
   archive, publish, or launch campaigns during setup.
5. Report the install location, checks that actually passed, and any single next action
   I must take. Never treat configuration alone as a working connection.
```

Prefer a Copy button? Right-click [the setup HTML](https://github.com/Aduomas/outcraft-cli/raw/refs/heads/main/assets/setup.html), choose **Save link as**, save it as `setup.html`, then open it locally. [HTML source](assets/setup.html).

**Login requires an Outcraft email and password.** Google sign-in, MFA and CAPTCHA solving are not automated. Never paste credentials into chat. A browser-only chat cannot install a local stdio MCP server.

## Use it

Once setup is verified, ask your agent: “List my Outcraft campaigns” or “Show the sequence for campaign 123.” For changes, name the campaign and the exact edit.

CLI writes need `--yes`; MCP writes need `confirm: true`. Campaign `delete` means archive, not permanent deletion. Publication and launch are separate actions.

<details>
<summary>Manual setup and CLI commands</summary>

Requires Python 3.11+. From a checkout of this repo:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[browser,mcp]'
python -m playwright install --only-shell chromium
cp -n .env.example .env
chmod 600 .env
```

Linux may need Chromium system libraries: `python -m playwright install-deps chromium` can require administrator access. Fill in `OUTCRAFT_EMAIL` and `OUTCRAFT_PASSWORD` locally in `.env`, not in chat. Do not source the file as a shell script.

```bash
outcraft --env-file .env auth login
outcraft campaigns list
outcraft campaigns show 123 --section sequence
outcraft campaigns rename 123 'New campaign name' --yes
outcraft --help
```

Playwright opens a temporary browser for login, then closes it. HTTPX handles campaign requests. No personal Chrome profile or stealth browser is used. Sessions are saved locally with owner-only permissions; `outcraft auth logout` removes the local session. `--dry-run` makes no requests.

</details>

<details>
<summary>Manual MCP registration</summary>

Use absolute paths for both the executable and `.env`. Keep credentials out of MCP configuration. For Claude Code:

```bash
claude mcp add --transport stdio --scope user outcraft -- /absolute/path/outcraft-cli/.venv/bin/outcraft-mcp --env-file /absolute/path/outcraft-cli/.env
```

For Codex:

```bash
codex mcp add outcraft -- /absolute/path/outcraft-cli/.venv/bin/outcraft-mcp --env-file /absolute/path/outcraft-cli/.env
```

For a host that accepts `mcpServers` JSON, merge this entry without replacing other servers:

```json
{"mcpServers":{"outcraft":{"command":"/absolute/path/outcraft-cli/.venv/bin/outcraft-mcp","args":["--env-file","/absolute/path/outcraft-cli/.env"]}}}
```

The stdio server opens no listening port and logs in only when needed. It supports campaign reads, creation, editing, archive, duplication, pause/resume, publication and discovered dashboard actions. Restart or start a new agent session if required, then verify tool discovery and a read-only campaign request.

</details>

## Verification limits

Browser-session handoff to HTTPX is verified against Outcraft's login validation endpoint. Real email/password login and authenticated campaign writes still need live verification; no credentials have been supplied. A successful submission is not proof of publication or launch.
