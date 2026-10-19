---
name: outcraft
description: Read and manage Outcraft outreach campaigns through the local outcraft CLI. Use when the user mentions Outcraft, or asks to list, show, export, create, rename, edit, pause, resume, archive, duplicate or publish a campaign, or asks about its leads or analytics.
---

# Outcraft CLI

Run every command as:

```bash
~/outcraft-cli/.venv/bin/outcraft --env-file ~/outcraft-cli/.env <group> <command> [args]
```

- Global flags (`--env-file`) go before the group. The CLI logs in on demand from that file.
- stdout = JSON result. stderr = `{"error": ...}`, exit 1.
- Flags for any command: append `--help`. Section names: `campaigns sections`.

## Read

| Need | Command |
| --- | --- |
| Campaign list | `campaigns list` (`--search`, `--page N`, `--archived`) |
| One section | `campaigns show <id> --section <section>` (default `instructions`) |
| Everything | `campaigns export <id>` |
| Leads per status | `leads count` (`--campaign <id>`) |
| Lead list | `leads list` (`--campaign <id>`, `--status <status>`, `--page N`, `--per-page 10\|25\|100`) |
| One lead: runs and interactions | `leads show <lead-id>` |
| Funnel and revenue | `analytics` (`--campaign <id>`, `--range 24h\|7d\|14d\|30d\|360d`) |
| Unknown page or action | `inspect <dashboard-path>` |

Look up IDs with `campaigns list --search <name>`; ask the user only when several match.

## Write

1. Scope = the exact change the user asked for, on a named campaign. Anything wider: ask first.
2. `update` and `action`: run with `--dry-run` first (sends nothing) and show the user the plan.
3. Run with `--yes`.
4. Read back with `campaigns show <id> --section <section>`; report the persisted state.

Failed or interrupted write: read back, report, then wait for the user. `resume` can contact real leads, so a blind retry is never safe.

| Word | Meaning |
| --- | --- |
| `delete` / `archive` | Archive. Recoverable, never permanent. |
| `publish` | Saves the campaign. Leads are contacted only once it runs. |
| `resume` / `pause` | Starts or stops sending. |

## Errors

| stderr | Fix |
| --- | --- |
| `Credential file must be private` | `chmod 600 ~/outcraft-cli/.env` |
| Login fails or `Not logged in` | Ask the user to check `OUTCRAFT_EMAIL` and `OUTCRAFT_PASSWORD` in `~/outcraft-cli/.env`. Google sign-in, MFA and CAPTCHA are unsupported. |
| Cloudflare page or `blocked` | Stop and report. Outcraft blocked the request. |
| `... Inspect before retrying` | Read back the campaign; report what persisted. |

Credentials live only in the `.env` file: read the CLI's output, never the file's values.
