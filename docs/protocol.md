# Observed protocol: 19 October 2026

Outcraft's campaign dashboard uses server-rendered Laravel Livewire, not a stable public campaign REST API. This client follows the dashboard's authenticated requests, so a dashboard release can break it.

## Login and request lifecycle

1. Playwright opens `/auth/login` in a fresh headless Chromium context, without a persistent or personal browser profile.
2. The browser submits the email/password form, including Outcraft's email-first step when present. It transfers origin-scoped cookies and browser identity headers to HTTPX, then closes. HTTPX must verify the authenticated campaign index before login is reported as successful.
3. HTTPX handles all subsequent requests. Persist cookies, including rotations, and browser identity headers together in an owner-only session file. Preserve each server snapshot byte-for-byte and send property changes separately in `updates`.
4. Replace the snapshot after every reply. Livewire 4 returns HTML under `effects.html` or named `effects.partials`, and may return a redirect, events, or validation errors.
5. Re-fetch the relevant page to verify durable state after a mutation. Draft saving, publishing, and running are separate outcomes.

Request envelope:

```json
{
  "_token": "<csrf>",
  "components": [{
    "snapshot": "<original signed JSON string>",
    "updates": {},
    "calls": [{"method": "mountAction", "params": ["createNewCampaign"], "metadata": {}}]
  }]
}
```

Nested PHP arrays and collections are represented as `[value, {"s":"arr"}]` or other synthesizer metadata. Reads unwrap them; outgoing signed snapshots are never reconstructed from unwrapped data.

## Campaign list

`/campaigns` has `createNewCampaign`, `edit_name`, `edit_tags`, `toggle_campaign`, `duplicate`, `archive`, and `campaign_audit_log`. Record actions pass `mountAction(name, {}, {"recordKey":"<id>","table":true})`. A mounted form submits `callMountedAction` with `mountedActions.0.data.<field>` updates.

Creation and rename both use `name`, with the observed UI requiring 3 to 191 characters. `/campaigns/archived` initially had 0 records. The approved test later exposed archived cards as `div[wire:key]` with `.table.records.<id>`, no campaign link, and a `mountAction('unarchive', {}, {"recordKey":"<id>","table":true})` Re-Activate action. Permanent deletion was not observed. `campaigns delete` is explicitly an archive alias, not a claim of permanent deletion.

## Campaign setup

All paths below are relative to `/campaigns/<id>/onboarding/`:

| CLI section | Path | Observed mechanism |
| --- | --- | --- |
| source | onboarding redirect | Current provider or incomplete setup, never hardcoded to API |
| brand | company | Brand selection |
| instructions | campaign/context | `data` fields, next-step save event |
| general | campaign/general | `data` fields, next-step save event |
| schedule | campaign/schedule | `data` fields, next-step save event |
| channels | campaign/channels | `data` fields and configuration actions |
| sequence | campaign/sequence | Table `addStep`, `edit`, `delete`, reorder |
| evaluations | campaign/evaluations | Add, edit, remove actions |
| conditions | conditions | Branch rules in `data.branches` |
| agent | agent | Agent fields and language actions |
| finish | finish | Review, publication controls |
| countries | countries | Country table and enable/disable actions |
| overrides | overrides | Rule table with create/edit actions |

The Continue request was intercepted and aborted before reaching the server: `__dispatch` with parameters `["move-to-next-step", {}]`. Section updates use `data.<field>` in the same call. The topbar's publication method is `publishKnowledgebaseDraft`; it is distinct from campaign pause/resume.

## New campaign objective and provider

A new campaign initially opens `/campaigns/<id>/onboarding`. Its component has `data.template_id`, `data.provider`, `data.direction`, and `step`. Radio field IDs and values expose the current objective/provider choices. The observed Continue method is `continue`: step 1 selects the objective, step 2 selects the provider, and the form also exposes `submitTemplateSelectFormWithIntegration`.

The approved test selected template 5 (Send Information) and provider `csv` (CSV File / Manual), then redirected to the brand page with setup still incomplete. `inspect` and low-level `call` expose this contract without adding an endpoint or assuming an API source. Authentication and Cloudflare checks still apply to every request.

## Sequence fields

An observed email edit mounted action used `relative_delay_minutes`, `relative_delay_unit` (`minutes`, `hours`, `days`), `is_customized_email_enabled`, `customized_email_subject`, and `customized_email_body`. Despite its name, `relative_delay_minutes: 1` with unit `days` means 1 day. The email subject and body use rich-text document JSON, not just strings.

Never replay a snapshot captured from another account or session, or infer a method name from a label. The inspect command exposes the currently rendered action expressions and form fields instead.

## Verification limits

The initial inspection was read-only: 5 active-list campaigns and 0 archived campaigns. After explicit approval, empty test campaign 1782 was created, renamed, configured with Send Information and a manual CSV source, then archived in Chrome, without leads or launch. The original urllib client could read authenticated pages but its POSTs returned Cloudflare HTTP 403. See [live verification](verification.md).

Later, standard Playwright passed 3 fresh-session blank-login checks. Transferring its cookies and request headers to HTTPX produced the expected server validation response in 3 of 3 checks, including after Chromium closed; fresh HTTPX without that session returned 403 in all 3 controls. These checks used no real credentials and do not prove successful account login or authenticated campaign writes. CLI mutations require `--yes`, MCP mutations require `confirm: true`, and CLI dry runs make no requests. Neither client silently retries a failed mutation.

Primary references: [Livewire hydration](https://livewire.laravel.com/docs/4.x/hydration), [Livewire JavaScript protocol](https://livewire.laravel.com/docs/4.x/javascript). Outcraft-specific facts come from the signed-in Chrome tab, rendered contracts, and captured network requests.
