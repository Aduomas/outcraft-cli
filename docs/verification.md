# Live verification: 19 October 2026

**The implemented browser-to-HTTPX handoff passed 3 live checks; authenticated campaign editing remains unverified.** Standard Playwright bootstrapped a fresh session, closed Chromium, and the production HTTPX transport reloaded that private session and received HTTP 200 with the expected blank-email validation error in all 3 checks.

No real login credentials were used. These checks do not prove successful password authentication, publication or campaign writes. CLI and MCP login/read/edit flows are additionally exercised against synthetic local dashboards using real Chromium and the MCP stdio protocol.

## Earlier dashboard and urllib verification

| Check | Result | Evidence source |
| --- | --- | --- |
| Public email/password handshake | 1 login component, CSRF token and discovered update URI parsed | CLI HTTP transport against `/auth/login` |
| Authenticated campaign list | 5 existing campaigns read, names and statuses matched Chrome | CLI GET `/campaigns` with a private existing session |
| Direct write attempt | HTTP 403, page title `Attention Required! / Cloudflare`, text `Sorry, you have been blocked` | CLI rename-form mount and objective-selection POST |
| Empty verification campaign | ID 1782 created, renamed, objective and manual source configured, then archived | Authorized Chrome workflow, re-opened after changes |
| Outreach safety | 0 leads imported, 0 campaign launches, existing 5 campaign statuses unchanged | Setup remained incomplete, final campaign list matched baseline |

The temporary first-party cookie copy was used only for these earlier checks, never committed or printed, and was deleted afterward. The implementation now uses temporary Playwright contexts, not a copied personal Chrome session. No stealth browser, CAPTCHA solver or proxy rotation is included. Password login success is not claimed because no password was supplied.

## Provider boundary

Outcraft's [official API reference](https://developers.outcraft.ai/docs) documents bearer-key access for lead retrieval, lead-campaign retrieval/cancellation and event triggers. It does not expose the dashboard's campaign configuration/create/archive operations in the documented operation list.

The old urllib POST failures do not prove that browser-bootstrapped HTTPX requests will fail: the later blank-login handoff succeeded. Valid credentials and read-back verification of an authorized empty test campaign are still required before claiming end-to-end account editing. If Outcraft blocks an authenticated request, the client stops rather than retrying a mutation; supported provider endpoints remain the more stable integration route.

## Implementation review

- **Standards:** added action/credential tests, fixed empty environment-variable precedence, removed the fresh-clone env-file claim, normalized campaign names before sending/comparing them, and removed an unused pagination parameter.
- **Intent:** fixed page-2 target resolution, nested component ownership and named-partial rendering; added the real empty-PHP-array and archived-card shapes as regression cases.
- **Docs:** campaign deletion is archive, publishing is distinct from running, low-level operations report unverified outcomes, source inspection follows the campaign's current onboarding redirect.
