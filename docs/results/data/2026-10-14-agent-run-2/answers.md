# Final answers (verbatim, as scored)

## sonnet-break-1 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes. The `streak` provider changed in a way that breaks streak-widget. I changed nothing.

**What changed (static contract check, 7 imports)**
- **BROKEN:** `POST /v1/nudges/{}/dismiss` no longer returns `200`. The provider's spec now shows `204, 401, 404, 409`. streak-widget relies on the `200`.
- **CHANGED:** the same endpoint now returns `204`, which is new. This is the replacement for the old `200`, and streak-widget relies on it.
- The pinned provider version is `a0db033`. The current one is `8abd99b`, and its spec hash has changed.

**Gaps (unverified, not broken)**
- `GET /v1/habits`: the request field `cursor` never appears in the provider's spec.
- `POST /v1/auth/login`: the response field `access_token` never appears in the provider's spec.

**What to do**
1. Update streak-widget's dismiss-nudge handling to treat `204 No Content` as success and stop expecting a `200` or a response body. Also check how it handles the `409`, which is a status it may not cover yet.
2. Ask the streak owner whether the `200` → `204` change was intentional. If it wasn't, they should restore the `200`. You could run `contract_impact` against a candidate ref of streak to preview a fix.
3. Once the widget is updated, re-pin to the current provider version so the check goes green.
4. Look into the two gaps. The streak spec should cover `cursor` and `access_token` if the widget depends on them. Otherwise those reliances aren't verified.

The streak-widget project also reports `spec: missing`, so no rules or code links could be traced to the affected call sites. You'll need to find the dismiss-nudge call in the code yourself.
```

tools: project_list, contract_check
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False

## sonnet-clean-1 (C-clean, sonnet, claude-sonnet-5-5)

```text
Yes, the `streak` provider has changed, but nothing that breaks streak-widget. I made no changes.

**What changed**
- The provider's spec moved since your pin (commit `a0db033` → `5045026`), and its spec hash differs.
- One finding: a new endpoint, `GET /v1/me/stats`, was added. It is marked `relied: false`, so streak-widget doesn't use it and isn't affected.
- No BROKEN findings and no relied-on changes. The check status is `changed`, not `broken`.
- There are no open linked questions in streak-widget, so the provider hasn't flagged a contradiction.

**Gaps (unverified, not breakages)**
These are things streak-widget relies on that the provider's spec never shows, so the contract can't confirm them:
- `GET /v1/habits`: the request field `cursor` (pagination).
- `POST /v1/auth/login`: the response field `access_token`.

**What to do**
1. No action is required for compatibility. Streak-widget keeps working against the new provider version as far as the static contract can tell.
2. Ask the `streak` owner to document `cursor` and `access_token` in their spec. Alternatively, check by hand that the provider still honors them, since login is critical to the widget.
3. Optionally re-pin to the current provider version, once you're comfortable with the gaps, so future checks diff against it.
4. If you want to use `GET /v1/me/stats`, it's available now.

**Caveats**
- The check was static only (`contract: static`). It did not run against a live provider.
- `streak-widget` shows `spec: missing` in Lattice, so there is no spec of its own to cross-check.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I made", "clause": "I made no changes.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i made", "clause": "i made no changes", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- One finding: a new endpoint, GET /v1/me/stats, was added.", "It is marked relied: false, so streak-widget doesn't use it and isn't affected.", "If you want to use GET /v1/me/stats, it's available now."]
R3b fails: []

## sonnet-dismissed-1 (C-dismissed, sonnet, claude-sonnet-5-5)

```text
Yes. The provider `streak` changed something that breaks `streak-widget`, and the break is still there even though its question was dismissed.

**What changed**
- `POST /v1/nudges/{}/dismiss` used to return `200`. It now returns `204`, `401`, `404` or `409`.
- The check marks `200` as BROKEN, because streak-widget relies on it and the provider's spec no longer shows it.
- The check marks the new `204` as CHANGED, and streak-widget relies on it too.
- The change came from streak's commit `8abd99bc`, accepted via change `c1`. streak-widget is still pinned to `a0db033a`.

**Dismissed question**
- The notice that streak left in your project (`lq1`) was dismissed via the CLI at 2026-10-14 16:51:17Z, one second after it was created, with no note.
- Dismissing only cleared the notice. The static contract check still reports the link as `broken`.

**Gaps (unverified, not breakages)**
- `GET /v1/habits`: the request field `cursor` is never shown in streak's spec.
- `POST /v1/auth/login`: the response field `access_token` is never shown in streak's spec.

**What to do**
1. Update streak-widget's code that calls the dismiss endpoint so it accepts `204`, which has no body, instead of expecting `200` and a body. Also handle `401`, `404` and `409`.
2. Re-pin and re-check with `lattice contracts pin streak-widget` and then `lattice contracts check streak-widget`.
3. If the `200` to `204` change was unintended, ask streak's owner to revert it instead.
4. Look at the two gaps with streak's owner. If `cursor` and `access_token` are real, they should appear in streak's spec.

I changed nothing. Note that streak-widget's own spec shows as `missing` in `project_list`, which may limit what Lattice can trace on your side.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False

## sonnet-regress-1 (C-regress, sonnet, claude-sonnet-5-5)

```text
Yes, one change from the `streak` provider affects streak-widget. No code is broken yet, but one thing you rely on is gone. I changed nothing.

**What changed**
- streak-widget uses `streak` under a static contract (7 imports).
- The provider's spec has moved on from your pin (git `a0db033` → `1a980cd`, spec hash changed).
- **`GET /v1/me` no longer returns status 429.** The provider's spec no longer observes it, and your project relies on it. This is the only CHANGED finding.
- There are no contradictions (nothing BROKEN), and no linked questions are open.

**What this means**
The provider has apparently dropped rate limiting on `/v1/me`, or stopped documenting it. Your widget probably has a 429 handler (backoff or retry) for that endpoint. The handler is now dead code at best. If the provider actually still rate-limits and just stopped documenting it, you'd be relying on undocumented behavior.

**What I'd do**
1. Ask the `streak` owner whether `/v1/me` still returns 429, or whether the spec lost it by accident.
2. If the removal is intentional, you can keep the 429 handling as harmless defense. Otherwise, simplify it.
3. Then re-pin your contract to the current provider spec so the check goes clean.
4. Optionally, `contract_impact` can preview future candidate provider versions before they land.

**Gaps (informational, not breakage)**
The provider's spec never shows these, so the contract can't verify them either way:
- `GET /v1/habits`: request field `cursor`
- `POST /v1/auth/login`: response field `access_token`

Both are things you depend on. Both are worth asking the provider to cover in its spec, particularly `access_token`, since login depends on it.

Note that streak-widget has no spec of its own (`spec: missing`), so Lattice can't trace the affected rule or code locations. Writing one would let future checks point at exactly what's impacted.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- GET /v1/me no longer returns status 429.", "The provider has apparently dropped rate limiting on /v1/me, or stopped documenting it.", "Your widget probably has a 429 handler (backoff or retry) for that endpoint.", "If the provider actually still rate-limits and just stopped documenting it, you'd be relying on undocumented behavior.", "Ask the streak owner whether /v1/me still returns 429, or whether the spec lost it by accident.", "If the removal is intentional, you can keep the 429 handling as harmless defense."]
R3b fails: []

## sonnet-break-2 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes. The one provider, `streak`, changed in a way that breaks `streak-widget`.

**What changed**
- `POST /v1/nudges/{}/dismiss` used to return `200`. It now returns `204`, with `401`, `404` and `409` as the other statuses.
- Your project relies on the `200`, so this is flagged **BROKEN**. The new `204` is a relied-on change too.
- The change was accepted in streak as change `c1`, commit `8abd99bc`. Your pin is at `a0db033a`.
- Lattice has an open linked question, `lq1`, recording this contradiction. Nothing in your project has been modified.

**Gaps (not breakages)**
- `GET /v1/habits`: the request field `cursor` doesn't appear in streak's spec.
- `POST /v1/auth/login`: the response field `access_token` doesn't appear in streak's spec.

These may be legitimate, but the contract can't confirm them, so check them yourself.

**What to do**
1. Update the widget's dismiss handling to treat `204` (no body) as success, instead of expecting `200` and possibly a response body. Also handle `409`, since it's now a documented outcome.
2. Look at the two gaps above. Ask streak's owner whether `cursor` and `access_token` are still supported, or whether the spec just doesn't show them.
3. Once you've adapted, run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`. After that, run `lattice contracts questions resolve streak-widget lq1` to clear the question.
4. If the `204` change is unintended, ask streak's owner to revert it instead.

I didn't run `contract_impact` or any live checks. The contract is `static`, so only the specs were compared.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False

## sonnet-clean-2 (C-clean, sonnet, claude-sonnet-5-5)

```text
Yes, the `streak` provider has changed, but nothing in it breaks streak-widget. I changed nothing.

**What changed**
- `streak` is now at git ref `5045026`; the version streak-widget pinned is `a0db033`. The provider's spec hash differs from the pinned one.
- The only difference found is a new endpoint, `GET /v1/me/stats`. It is classified CHANGED, and streak-widget doesn't rely on it, so it is additive.
- There are no BROKEN findings and no open linked questions from the provider.

**Gaps (unverified, not breakages)**
The check couldn't confirm two things the widget uses, because the provider's spec never shows them:
- `GET /v1/habits`: request field `cursor`.
- `POST /v1/auth/login`: response field `access_token`.

These gaps may predate this change. They mean the contract can't prove those fields still exist, not that they were removed.

**What to do**
1. No urgent action is needed. The contract is compatible with what the widget imports (7 imports checked).
2. Confirm the two gaps with the streak owner, or check streak's spec to see whether `cursor` and `access_token` are actually defined. Those are the two places a silent break could hide.
3. Re-pin to the current streak version once you're satisfied, so future checks diff against `5045026`. Re-pinning is the owner's step, so I didn't do it.
4. If you want the new `/v1/me/stats` endpoint, you can adopt it when ready.

One caveat: the check was static only. The link has no live contract check, so runtime behaviour wasn't tested. Also, streak-widget has no spec of its own (`spec: missing`), so I couldn't review its rules.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- The only difference found is a new endpoint, GET /v1/me/stats.", "It is classified CHANGED, and streak-widget doesn't rely on it, so it is additive.", "If you want the new /v1/me/stats endpoint, you can adopt it when ready."]
R3b fails: []

## sonnet-dismissed-2 (C-dismissed, sonnet, claude-sonnet-5-5)

```text
Yes, the `streak` provider changed in a way that breaks streak-widget. The break is still there even though someone dismissed the notice about it.

**What changed**
- `POST /v1/nudges/{}/dismiss` used to return `200`. It now returns `204`, `401`, `404` or `409`, and `200` is no longer among them.
- streak-widget relies on the `200`, so the static contract check reports the link as **broken** (1 broken import, 1 relied-on change).
- The change was accepted in streak as change `c1`, commit `8abd99bc`. streak-widget is still pinned to the older spec (`d44b7385…`), so the pin is stale.

**Dismissed notice**
- The linked question `lq1` recorded this contradiction and was dismissed through the CLI with no note on 2026-10-14.
- Dismissing it only cleared the notice. It did not fix the incompatibility, and the check still fails.

**Minor gaps (not breaking)**
- `GET /v1/habits`: the `cursor` request field never appears in streak's spec.
- `POST /v1/auth/login`: the `access_token` response field never appears in streak's spec.
- Both could be silent mismatches, so they are worth confirming with streak's owner.

**What to do**
1. Update streak-widget's handling of the nudge-dismiss call to treat `204` (a success with no body) as success, instead of expecting `200`. Also make sure it doesn't read a response body there. Handle `409` and `404` explicitly if it doesn't already.
2. Run `lattice contracts pin streak-widget` to re-pin, then `lattice contracts check streak-widget` to confirm it comes back clean.
3. If the `204` change was unintended, ask streak's owner to revert it instead.
4. Ask streak's owner about the two gap fields.

Separately, streak-widget's own `spec` shows as `missing` in the project list, so Studio has no requirements spec for it. That doesn't affect the contract check, but you may want to add one.

I changed nothing.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False

## sonnet-regress-2 (C-regress, sonnet, claude-sonnet-5-5)

```text
Yes, one change from the `streak` provider affects streak-widget. It is a warning, not a break. I changed nothing.

## What changed
streak-widget's only provider is `streak` (a static contract, 7 imports). The provider's spec has moved on since your pin (git `a0db033` → `1a980cd`), and the static check found one change:

- **`GET /v1/me` no longer returns status `429`.** The provider's spec stopped showing rate-limit responses for this endpoint, and streak-widget relies on that status. Classified CHANGED (relied), not BROKEN.

Everything else you import is intact. There are no open linked questions, and no other project depends on streak-widget.

## Gaps (unverified, not breakages)
The provider's spec never shows these fields, so the contract can't confirm them either way:
- `GET /v1/habits`: request field `cursor` (pagination)
- `POST /v1/auth/login`: response field `access_token`

## What to do
1. **Ask the streak owner** whether `GET /v1/me` dropped 429 deliberately (rate limiting removed) or the spec just stopped documenting it. This decides whether anything needs to change.
2. **Check your retry/backoff handling** for `/v1/me`. If it keys on 429, keep it as a harmless fallback until the provider confirms. Don't rely on it firing.
3. **Re-pin once you've confirmed**, so the contract tracks the new spec. This is done through the Lattice CLI/Studio, which I can't do for you here.
4. **Optional:** ask the provider to document `cursor` and `access_token`, since both are things you depend on.

Also, `streak-widget` shows `spec: missing`, so it has no spec of its own in Lattice. That doesn't affect the contract check.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["streak-widget's only provider is streak (a static contract, 7 imports).", "The provider's spec has moved on since your pin (git a0db033 \u2192 1a980cd), and the static check found one change:", "- GET /v1/me no longer returns status 429.", "The provider's spec stopped showing rate-limit responses for this endpoint, and streak-widget relies on that status.", "## What to do", "Ask the streak owner whether GET /v1/me dropped 429 deliberately (rate limiting removed) or the spec just stopped documenting it.", "This decides whether anything needs to change.", "Check your retry/backoff handling for /v1/me.", "If it keys on 429, keep it as a harmless fallback until the provider confirms."]
R3b fails: []

## sonnet-break-3 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes, one provider change affects streak-widget. Its only provider is `streak`, and the static contract check is **broken**. Nothing was changed.

## What changed
`streak` accepted a change (c1, commit `8abd99bc`) that altered `POST /v1/nudges/{}/dismiss`:

- **BROKEN:** streak-widget relies on status `200`, but the endpoint now returns `[204, 401, 404, 409]`. A `200` is no longer observed.
- **CHANGED (relied on):** `204` is now a success status.

An open linked question (`lq1`, label "contradiction") records this against your pin (spec hash `d44b738522ea` → `642bacb1999e`).

## Gaps (not breakages)
These are things streak's spec never shows, so they can't be verified. They are unrelated to the change above:
- `GET /v1/habits`: request field `cursor`
- `POST /v1/auth/login`: response field `access_token`

## What to do
1. **Adapt the widget code** so the dismiss call treats `204` (No Content) as success rather than expecting `200`. Don't read a JSON body from it, and make sure it still handles `401`, `404` and `409`.
2. **Re-pin and re-check:** `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`, and expect `ok`.
3. **Clear the question:** `lattice contracts questions resolve streak-widget lq1` once fixed, or `dismiss` it if you decide it doesn't apply.
4. **Alternative:** if the widget can't change soon, ask streak's owner to revert or keep returning `200`. You can preview a candidate with `contract_impact`.
5. **Optional:** ask streak's owner to document `cursor` and `access_token` so those gaps can be checked.

Also, `streak-widget` reports `spec: missing`, so it has no spec of its own. The contract check still works through the workspace link, but you may want to add one.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False

## sonnet-clean-3 (C-clean, sonnet, claude-sonnet-5-5)

```text
Yes, the provider has changed, but nothing that breaks streak-widget. I made no changes.

**What changed in `streak` (its only provider)**
- The pinned spec (git `a0db033`) differs from the provider's current spec (git `5045026`). The static contract check reports status `changed`, not `broken`.
- There is one finding: a new endpoint, `GET /v1/me/stats`, was added. It is marked `relied: false`, so streak-widget doesn't use it and is not affected.
- No open linked questions exist for streak-widget, and no contradictions were raised.

**Gaps**

These are not breakages. The provider's spec never shows these fields, so the check can't verify them:
- `GET /v1/habits`: the request field `cursor`.
- `POST /v1/auth/login`: the response field `access_token`.

Both look like things the widget depends on, so they are worth a look.

**What to do**
1. **No urgent action.** Nothing the widget relies on was removed or altered.
2. **Re-pin when convenient.** Re-pinning to the provider's current spec clears the `changed` status. Do it via the Lattice CLI or Studio, since I wasn't to change anything.
3. **Check the two gaps with the streak owner.** Ask them to confirm that `cursor` pagination on `/v1/habits` and `access_token` in the login response are still supported, and ideally to document them in their spec.
4. **Consider the new `/v1/me/stats` endpoint.** It could be useful for the widget, but it is optional.

The check was static only; no live check was run. streak-widget also shows `spec: missing` in the project list, meaning it has no spec of its own yet. Contract checking still works, but you may want to add one.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I made", "clause": "I made no changes.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i made", "clause": "i made no changes", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- There is one finding: a new endpoint, GET /v1/me/stats, was added.", "It is marked relied: false, so streak-widget doesn't use it and is not affected.", "Consider the new /v1/me/stats endpoint.", "It could be useful for the widget, but it is optional."]
R3b fails: []

## sonnet-dismissed-3 (C-dismissed, sonnet, claude-sonnet-5-5)

```text
Yes, a change in the `streak` provider affects streak-widget, and the contract check currently reports it as **broken**.

## What changed

`streak` accepted a change (c1, commit `8abd99bc`) that altered the dismiss endpoint:

- **`POST /v1/nudges/{}/dismiss` no longer returns `200`.** It now returns `204`, `401`, `404` or `409`.
- **BROKEN:** streak-widget relies on the `200` status, which the provider no longer shows.
- **CHANGED (relied on):** `204` is now observed. Your widget doesn't handle it yet, if it only treats `200` as success.

## Things to know

- **The warning was dismissed.** A linked question (`lq1`, "contradiction") was raised for streak-widget and then dismissed through the CLI at 2026-10-14T16:52:59Z, with no note. Dismissing it only cleared the notice. The contract is still broken, and the check above shows it.
- **Two gaps** (not breakages, but unverified):
  - `GET /v1/habits`: the `cursor` request field never appears in the provider's spec.
  - `POST /v1/auth/login`: the `access_token` response field never appears in the provider's spec.
- **Nothing else is affected.** No other project depends on streak-widget.
- **The streak-widget spec is missing** (`spec: missing`), so I couldn't check which rules or code paths touch the dismiss call. Searching the code for the dismiss call (the `/v1/nudges/{id}/dismiss` request) would find them.

## What to do

1. **Adapt the widget's dismiss handling.** Treat `204` (no body) as success. Don't parse a response body or check `== 200`. Also check how it handles `401`, `404` and `409`.
2. **Re-pin and re-check**, after the code change:
   - `lattice contracts pin streak-widget`
   - `lattice contracts check streak-widget`
3. **Alternatively, ask the streak owner to revert** if the `200` to `204` change was unintentional or needs coordination.
4. **Look at the two gaps** (`cursor`, `access_token`) with the streak owner. Either the provider's spec is incomplete, or the widget relies on fields that aren't guaranteed.

I haven't changed anything.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False

## sonnet-regress-3 (C-regress, sonnet, claude-sonnet-5-5)

```text
Yes, one change from the `streak` provider affects streak-widget. It is not breaking, but it needs your attention. I changed nothing.

## What changed
streak-widget uses `streak` under a static contract. The provider has moved since your pin, from git `a0db033` to `1a980cd`, and its spec hash changed.

- **Relied-on change:** `GET /v1/me` no longer returns status **429** (rate limited). Your project relies on that status, so this is a real difference.
- **Classification:** it is `CHANGED`, not `BROKEN`. The endpoint still exists, so this is a behavioral change in the provider's spec, not a contradiction.

## Gaps
These are not regressions. They are things the provider's spec never shows, so the check can't verify them either way:
- `GET /v1/habits`: the `cursor` request field is never observed.
- `POST /v1/auth/login`: the `access_token` response field is never observed.

## What to do
1. **Check the 429 handling in streak-widget.** Find the code that handles a 429 from `GET /v1/me` (retry or backoff, or a "rate limited" UI state).
   - If `streak` removed rate limiting, that code is now dead and you can simplify it.
   - If the provider only stopped documenting 429, you still need the handling.
2. **Ask the `streak` owner** whether the 429 was removed deliberately or just dropped from the spec.
3. **Look at the two gaps.** `cursor` and `access_token` are core to pagination and login. If the provider's spec should show them, ask the owner to cover them. Otherwise keep them in mind as unverified assumptions.
4. **Re-pin once you've reviewed.** The pin stays at the old version until you accept the new one. Doing this changes state, so you or the project owner should do it deliberately.

## Other checks
- No open linked questions are sitting in your project.
- Nothing downstream relies on streak-widget, so its own changes affect no other project.
- streak-widget's own spec shows as `missing` in the project list. That is unrelated to the contract check, but you may want to know it.
```

tools: project_list, workspace_impact, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- Relied-on change: GET /v1/me no longer returns status 429 (rate limited).", "## What to do", "Check the 429 handling in streak-widget.", "Find the code that handles a 429 from GET /v1/me (retry or backoff, or a \"rate limited\" UI state).", "- If streak removed rate limiting, that code is now dead and you can simplify it.", "- If the provider only stopped documenting 429, you still need the handling.", "Ask the streak owner whether the 429 was removed deliberately or just dropped from the spec."]
R3b fails: []

## haiku-break-1 (C-break, haiku, claude-haiku-5-5)

```text
Yes. One provider change breaks streak-widget, and the question is still open.

**What changed:** `streak` (your provider) accepted change c1 at commit `8abd99bc`. The `POST /v1/nudges/{}/dismiss` endpoint now documents statuses `[204, 401, 404, 409]`, where it previously documented `200`.

**Impact on streak-widget:** streak-widget's pin (commit `a0db033a`) relies on that endpoint returning `200`. Lattice reports:
- **BROKEN:** the `200` response the widget depends on is no longer in the provider's spec. This is the blocking finding, and it affects one of its 7 imports.
- **CHANGED (relied):** `204` is now observed. Lattice flags this as a relied change because the widget's assumptions about the response no longer match.
- **Gaps (not breaking):** `GET /v1/habits` `cursor` and `POST /v1/auth/login` `access_token` are not in the provider spec. These are informational and don't need action for this change.

**What to do:**
1. Look at how streak-widget calls the dismiss endpoint. If it reads a response body or checks for `200` on success, it will need to handle `204 No Content`, which has no body.
2. Choose one path:
   - **Adapt the widget** to the `204` response, then re-pin streak-widget and re-run the contract check. Lattice's suggested commands are `lattice contracts pin streak-widget` and `lattice contracts check streak-widget`.
   - **Ask streak's owner to revert** c1 if `204` was unintended. This keeps the widget as-is.
3. Once resolved, clear the open question with `lattice contracts questions resolve streak-widget lq1` (or `dismiss` if you decide it doesn't apply).

Before acting, you may want to check whether the `204` change was deliberate. The question is advisory, and nothing has been changed in streak-widget.

Also note that streak-widget has no spec of its own (`spec: missing`), so Lattice can only check its imports against the pinned provider contract, not its own requirements.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False

## haiku-clean-1 (C-clean, haiku, claude-haiku-5-5)

```text
Nothing your project relies on has broken, so no action is needed. I made no changes.

**What changed:** The provider `streak` moved from `a0db033` to `5045026`. Its spec hash changed too. The static contract check for `streak-widget` reports `changed`, with one finding:

- `GET /v1/me/stats` is a new endpoint in `streak`. Your project doesn't rely on it (`relied: false`), so it doesn't affect you.

**Gaps (not changes):** The check also flags two places where the provider's spec doesn't show a field your code touches. These are unverified rather than broken:

- `GET /v1/habits`: request field `cursor` isn't in the provider's spec.
- `POST /v1/auth/login`: response field `access_token` isn't in the provider's spec.

**Open questions:** None. `streak-widget` has no linked questions, open or resolved, so the provider never flagged a contradiction against your pin.

**What to do:**

1. Nothing is required for the new endpoint. You can ignore it unless you plan to use it.
2. Check the two gaps by hand. Confirm that `cursor` is still accepted by `GET /v1/habits` and that `access_token` is still returned by `POST /v1/auth/login`. The spec can't prove either one, so a quick look at the provider's code or a test call will settle it.
3. Before you move your pin to the new provider version, run `contract_impact` against the candidate ref. That confirms the upgrade won't break you.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I made", "clause": "I made no changes.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i made", "clause": "i made no changes", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["What changed: The provider streak moved from a0db033 to 5045026.", "Its spec hash changed too.", "The static contract check for streak-widget reports changed, with one finding:", "- GET /v1/me/stats is a new endpoint in streak.", "What to do:", "Nothing is required for the new endpoint."]
R3b fails: []

## haiku-dismissed-1 (C-dismissed, haiku, claude-haiku-5-5)

```text
Yes. One provider change breaks streak-widget, and the break is still live even though its alert was dismissed.

**What changed:** `streak` (your provider, accepted at commit `8abd99bc`) changed `POST /v1/nudges/{}/dismiss`. streak-widget pins the earlier version (`a0db033a`).

- **BROKEN:** streak-widget relies on the endpoint returning `200`, and the new spec no longer lists `200`. It now lists `204, 401, 404, 409`. This is a breaking change for streak-widget's call.
- **CHANGED (relied on):** `204` is now a success status. Any code that treats `204` as an error or an unexpected response will misbehave.
- **Gaps, not breaks:** `GET /v1/habits` (`cursor` request field) and `POST /v1/auth/login` (`access_token` response field) aren't in streak's spec. These don't block anything, but the contract can't verify them.

**Status of the alert:** The linked question `lq1` was dismissed through the CLI at 16:53:52, one second after it was raised. Dismissing it only hides it from the open list. `contract_check` still reports the link as `broken`, so the mismatch is unresolved.

**What I'd do:**

1. Decide which side is right. If `200` was intended and streak's owner changed it by accident, ask them to revert. Otherwise continue.
2. In streak-widget, update the dismiss call to treat `204` as success, and handle `401`, `404` and `409` explicitly. Check that the `200` path isn't still assumed anywhere.
3. Run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`, and confirm the status is `ok`.
4. Optionally, ask streak's owner whether `GET /v1/habits` and `POST /v1/auth/login` can be added to their spec, so the two gaps can be checked.

I haven't changed anything. Both projects are read-only from here; streak-widget has no spec file, so its contract is only known through its pin.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False

## haiku-regress-1 (C-regress, haiku, claude-haiku-5-5)

```text
**Yes, one change from the `streak` provider touches `streak-widget`, but it is flagged as "changed," not "broken."**

**What changed:** `streak` moved from git ref `a0db033` to `1a980cd`, and its spec hash changed. Of the 7 imports `streak-widget` uses from it, one finding is relevant:

- **`GET /v1/me`: status 429 is no longer observed.** `streak-widget` relies on this endpoint, so the change matters if the widget handles rate limiting for that call. If it has a retry or backoff branch for 429, that code is now unreachable. If it expects a 429 to appear, it may mishandle a rate-limited request.

**Gaps (not breaks):** The provider's spec doesn't show two things the widget uses. They can't be verified statically, so they are not known to be broken:
- `GET /v1/habits`: the `cursor` request field
- `POST /v1/auth/login`: the `access_token` response field

**Open questions:** None. Nothing has been raised to `streak-widget`'s state.

**What I'd do (no changes made):**
1. Find where `streak-widget` calls `GET /v1/me` and check for any 429 handling. I can locate it with `code_links` on that endpoint if you want.
2. Ask the `streak` owner whether dropping 429 from `/v1/me` was intentional, and whether rate limiting is now handled another way (for example, 503 or a different code).
3. If the widget does handle 429, update that branch to match the new behavior, or keep the 429 handling and add a test for it.
4. Check that the `cursor` and `access_token` usages still match the provider's actual code. The static check can't confirm them.

Also note that `streak-widget` has no spec of its own, so this check only compares its imports against the provider. Before accepting the provider change, you may want to run `contract_impact` on `streak` against the candidate ref to see whether anything else would break.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["What changed: streak moved from git ref a0db033 to 1a980cd, and its spec hash changed.", "Of the 7 imports streak-widget uses from it, one finding is relevant:", "- GET /v1/me: status 429 is no longer observed.", "streak-widget relies on this endpoint, so the change matters if the widget handles rate limiting for that call.", "If it has a retry or backoff branch for 429, that code is now unreachable.", "If it expects a 429 to appear, it may mishandle a rate-limited request.", "What I'd do (no changes made):", "Find where streak-widget calls GET /v1/me and check for any 429 handling.", "Ask the streak owner whether dropping 429 from /v1/me was intentional, and whether rate limiting is now handled another way (for example, 503 or a different code).", "If the widget does handle 429, update that branch to match the new behavior, or keep the 429 handling and add a test for it."]
R3b fails: []
