# Live verification: 19 October 2026

**Direct campaign writes are blocked by Cloudflare HTTP 403.** The client can read authenticated campaign pages using an existing first-party session, but it cannot currently replace the dashboard for campaign edits.

## Verified paths

| Check | Result | Evidence source |
| --- | --- | --- |
| Public email/password handshake | 1 login component, CSRF token and discovered update URI parsed | CLI HTTP transport against `/auth/login` |
| Authenticated campaign list | 5 existing campaigns read, names and statuses matched Chrome | CLI GET `/campaigns` with a private existing session |
| Direct write attempt | HTTP 403, page title `Attention Required! / Cloudflare`, text `Sorry, you have been blocked` | CLI rename-form mount and objective-selection POST |
| Empty verification campaign | ID 1782 created, renamed, objective and manual source configured, then archived | Authorized Chrome workflow, re-opened after changes |
| Outreach safety | 0 leads imported, 0 campaign launches, existing 5 campaign statuses unchanged | Setup remained incomplete, final campaign list matched baseline |

The temporary first-party cookie copy was used only for these checks, never committed or printed. There is no CAPTCHA, WAF or bot-protection bypass in this repository. Password login success is not claimed: no password was supplied, and the direct POST path is blocked.

## Provider boundary

Outcraft's [official API reference](https://developers.outcraft.ai/docs) documents bearer-key access for lead retrieval, lead-campaign retrieval/cancellation and event triggers. It does not expose the dashboard's campaign configuration/create/archive operations in the documented operation list.

To unblock direct campaign management, ask Outcraft to permit an authorized programmatic client for its Livewire write endpoints, or provide supported campaign-management API endpoints. A fresh password or another cookie copy alone is not a verified fix.

## Implementation review

- **Standards:** added action/credential tests, fixed empty environment-variable precedence, removed the fresh-clone env-file claim.
- **Intent:** fixed page-2 target resolution, nested component ownership and named-partial rendering; added the real empty-PHP-array and archived-card shapes as regression cases.
- **Docs:** campaign deletion is archive, publishing is distinct from running, low-level operations report unverified outcomes, source inspection follows the campaign's current onboarding redirect.
