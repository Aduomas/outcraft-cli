# Observed protocol: 19 October 2026

Outcraft's campaign dashboard uses server-rendered Laravel Livewire, not a stable public campaign REST API. This client follows the dashboard's authenticated requests, so a dashboard release can break it.

## Login and request lifecycle

1. GET `/auth/login` to obtain session cookies, the CSRF token, the login component's signed snapshot, and the current update URI.
2. POST to the discovered `script[data-update-uri]`, observed as `/livewire-9388d904/update`, calling `authenticate` with updates for `email`, `password`, and `rememberMe`.
3. Keep cookies, including rotations, and preserve each server snapshot byte-for-byte. Send property changes separately in `updates`.
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

Creation and rename both use `name`, with the observed UI requiring 3 to 191 characters. `/campaigns/archived` exists, but had 0 records during inspection: restore and permanent-delete contracts were not observed. `campaigns delete` is explicitly an archive alias, not a claim of permanent deletion.

## Campaign setup

All paths below are relative to `/campaigns/<id>/onboarding/`:

| CLI section | Path | Observed mechanism |
| --- | --- | --- |
| source | apps/api | Event table and edit actions |
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

## Sequence fields

An observed email edit mounted action used `relative_delay_minutes`, `relative_delay_unit` (`minutes`, `hours`, `days`), `is_customized_email_enabled`, `customized_email_subject`, and `customized_email_body`. Despite its name, `relative_delay_minutes: 1` with unit `days` means 1 day. The email subject and body use rich-text document JSON, not just strings.

Never replay a snapshot captured from another account or session, or infer a method name from a label. The inspect command exposes the currently rendered action expressions and form fields instead.

## Verification limits

The initial inspection was read-only: 5 active-list campaigns and 0 archived campaigns. No production campaign was started, paused, published, created, renamed, archived, or deleted during discovery. Mutating commands require explicit confirmation; dry runs do not POST.

Primary references: [Livewire hydration](https://livewire.laravel.com/docs/4.x/hydration), [Livewire JavaScript protocol](https://livewire.laravel.com/docs/4.x/javascript). Outcraft-specific facts come from the signed-in Chrome tab, rendered contracts, and captured network requests.
