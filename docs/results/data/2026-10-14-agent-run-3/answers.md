# Final answers (verbatim, as scored)

## sonnet-break-1 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes, a change in the `streak` provider has broken streak-widget. I changed nothing.

**What changed**
- `streak` accepted change `c1` (commit `8abd99bc`). streak-widget is pinned to the earlier spec at commit `a0db033a`.
- **BROKEN:** `POST /v1/nudges/{}/dismiss` no longer returns `200`. The spec now shows `204`, `401`, `404` and `409`.
- **CHANGED (relied on):** `204` is now a returned status on that same endpoint.
- This is one open linked question, `lq1`, labelled "contradiction". It affects 1 of streak-widget's 7 imports.

**What to do**
1. Update streak-widget's dismiss handling to treat `204` (no body) as success instead of expecting `200`. Check that nothing reads a response body from that call, and that `401`, `404` and `409` are handled.
2. Run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`, to confirm the contract is clean.
3. Run `lattice contracts questions resolve streak-widget lq1` to clear the note.
4. If you'd rather keep `200`, ask streak's owner to revert the change.

**Gaps, no action needed now**
Two things can't be verified because streak's spec never shows them:
- the `cursor` request field on `GET /v1/habits`
- the `access_token` response field on `POST /v1/auth/login`

Also, `project_list` reports streak-widget's own spec as "missing". That doesn't block the contract check, but it means there is no spec to trace the dismiss behaviour back to.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R9 default calls: []

## sonnet-clean-1 (C-clean, sonnet, claude-sonnet-5-5)

```text
Yes, the provider (`streak`) has changed since streak-widget's pin, but nothing it relies on is broken.

**What changed**
- The `streak` spec moved from the pinned version (git `a0db033`) to the current one (git `5045026`).
- The only finding is `CHANGED`: a new endpoint, `GET /v1/me/stats`, was added.
- That finding is marked `relied: false`, so streak-widget doesn't import it. None of its 7 imported claims stopped holding, so there are no `BROKEN` findings.
- There are no linked questions, open or otherwise. The provider hasn't left any notes about a breaking change.

**Gaps (unverified, not breakages)**
The contract check can't confirm two things the widget relies on, because the provider's spec never shows them:
- `GET /v1/habits`: the request field `cursor`.
- `POST /v1/auth/login`: the response field `access_token`.

**What to do**
1. Nothing is required. The widget is still compatible with the provider's current spec.
2. Optional: if you want the widget to use `GET /v1/me/stats` (for example, to show streak stats), add it as an import, then re-pin to the current provider version.
3. Optional: re-pin to the current provider version so the `CHANGED` finding clears.
4. Ask the `streak` owner to add `cursor` on `GET /v1/habits` and `access_token` on `POST /v1/auth/login` to their spec. Until then, those two reliances stay unverified. If you can't get that, treat them as a manual check.

Also, `project_list` reports streak-widget's own spec as `missing`. The check still ran from its declared imports, but it may be worth confirming its spec files are where Lattice expects them.

I changed nothing, as requested. These tools are read-only anyway, so re-pinning has to be done by you via the CLI or Lattice Studio.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing, as requested.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- The only finding is CHANGED: a new endpoint, GET /v1/me/stats, was added.", "Optional: if you want the widget to use GET /v1/me/stats (for example, to show streak stats), add it as an import, then re-pin to the current provider version."]
R3b fails: []
R3 (information) pass: True; NOOPEN: True; BREAK sentences: []
R3c CLAIMOPEN sentences: []
R9 default calls: []

## sonnet-dismissed-1 (C-dismissed, sonnet, claude-sonnet-5-5)

```text
Yes. The provider `streak` changed a contract your widget relies on, and the break is still live even though its question was dismissed.

**What changed**
- `POST /v1/nudges/{}/dismiss` no longer returns `200`. The provider's spec now shows `204`, `401`, `404` and `409`.
- Your widget's pinned contract (streak spec `d44b738522ea`, commit `a0db033`) relies on the `200`. The current one is `642bacb1999e`, commit `8abd99b`.
- `contract_check` reports the link as **broken**: 1 BROKEN finding (`200` not observed) and 1 CHANGED finding (`204` added, on an endpoint you import).

**Question status**
- The linked question `lq1` was **dismissed** via the CLI, with no note. That only cleared the advisory. The contract check still shows the break, so nothing was fixed.

**Gaps (unverified, not breaks)**
- `GET /v1/habits`: the provider's spec never shows the `cursor` request field.
- `POST /v1/auth/login`: the provider's spec never shows the `access_token` response field.

**What to do**
1. Update streak-widget's dismiss-nudge handling to treat `204` (no body) as success. Also handle `401`, `404` and `409` if you don't already.
2. Run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`, to confirm the link is clean.
3. If `200` was intentional on your side, ask streak's owner to revert or restore it instead.
4. Look at the two gaps. Confirm that `cursor` and `access_token` still exist in the provider's behaviour, or ask the owner to add them to the spec.

I changed nothing. Note that streak-widget itself shows `spec: missing`, so Lattice has no requirements spec for your project, only the contract link.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R9 default calls: []

## sonnet-regress-1 (C-regress, sonnet, claude-sonnet-5-5)

```text
Yes, one provider change affects streak-widget, but nothing is broken. Nothing was changed on my side.

**What changed**

streak-widget uses one provider, `streak`, under a static contract. The pinned provider version is `a0db033`. The provider is now at `1a980cd`, so its spec has changed since your pin. The contract check returned a status of **changed** (not broken), with 1 finding and 2 gaps across 7 imports.

- **Finding (CHANGED, relied on):** `GET /v1/me` no longer returns status **429**. The provider's spec no longer shows rate limiting on that endpoint, and streak-widget relies on the 429 response there.
- **Linked questions:** none. The provider has left no notes, and the open, resolved and dismissed counts are all 0.

**Gaps (unverifiable, not breakages)**

- `GET /v1/habits`: the request field `cursor` is never shown in the provider's spec.
- `POST /v1/auth/login`: the response field `access_token` is never shown in the provider's spec.

**What to do**

1. **Check how streak-widget handles 429 from `GET /v1/me`.** Ask the `streak` owner whether the 429 was removed deliberately (no more rate limiting there) or just dropped from the spec. Either way, keep your retry/backoff handling until that's confirmed. It is harmless if the 429 never comes, and it protects you if the spec simply lost it.
2. **Ask the `streak` owner to document `cursor` and `access_token`.** You depend on both, and while the spec doesn't show them nobody can check them. Your `access_token` dependency matters most, since login is on the critical path.
3. **Re-pin once you're satisfied.** After you've checked 1 and 2, update the pin in the workspace so the contract check is clean against the current provider version.

Two notes:
- streak-widget's own spec shows as `missing`, so I couldn't map the 429 reliance to specific code with the lattice tools. A grep of your widget code for `429` or retry handling on the `/v1/me` call is the quickest way to find it.
- This server is read-only, so the pin update and any spec changes are for you to make.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- Finding (CHANGED, relied on): GET /v1/me no longer returns status 429.", "The provider's spec no longer shows rate limiting on that endpoint, and streak-widget relies on the 429 response there.", "Check how streak-widget handles 429 from GET /v1/me.", "Ask the streak owner whether the 429 was removed deliberately (no more rate limiting there) or just dropped from the spec.", "It is harmless if the 429 never comes, and it protects you if the spec simply lost it.", "Two notes:", "- streak-widget's own spec shows as missing, so I couldn't map the 429 reliance to specific code with the lattice tools.", "A grep of your widget code for 429 or retry handling on the /v1/me call is the quickest way to find it."]
R3b fails: []
R3 (information) pass: True; NOOPEN: True; BREAK sentences: []
R3c CLAIMOPEN sentences: []
R9 default calls: []

## sonnet-break-2 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes. The `streak` provider has changed in a way that breaks streak-widget. I changed nothing.

## What changed

The static contract check shows the link as **broken**: 1 of the 7 imports is affected, and the one change is on `POST /v1/nudges/{}/dismiss`.

| Class | Finding |
|---|---|
| **BROKEN** | streak-widget relies on status `200`, but streak's spec now lists only `204`, `401`, `404` and `409`. |
| **CHANGED** (on an endpoint you import) | Status `204` is newly observed. |

Streak accepted this change as c1 (commit `8abd99bc`). That acceptance left an open linked question for you, `lq1`, labelled "contradiction". In practice the dismiss endpoint now returns `204 No Content` where it used to return `200`.

There are also two non-blocking gaps, where streak's spec never shows fields you use:
- `GET /v1/habits` request field `cursor`
- `POST /v1/auth/login` response field `access_token`

These aren't regressions, but they mean those claims can't be verified.

## What to do

1. **Adapt the widget's dismiss handling.** Treat `204` as success and don't expect a `200` or a response body. Any code that parses a JSON body from the dismiss call needs to stop doing so. Also check that you handle `401`, `404` and `409`.
2. **Re-pin and re-check:**
   - `lattice contracts pin streak-widget`
   - `lattice contracts check streak-widget`
3. **Clear the question** once it's addressed: `lattice contracts questions resolve streak-widget lq1`. Use `dismiss` instead if you decide it doesn't apply.
4. **Alternative:** if the widget can't change soon, ask streak's owner to revert or keep `200`. That's a negotiation with them, not something you can do from your side.
5. **Optional:** raise the two gaps with streak's owner so `cursor` and `access_token` appear in their spec.

Note that `streak-widget` has no spec of its own (`spec: missing`), and this server is read-only. The pin, resolve and dismiss steps have to be run from the CLI or Lattice Studio.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R9 default calls: []

## sonnet-clean-2 (C-clean, sonnet, claude-sonnet-5-5)

```text
Short answer: the `streak` provider has changed since streak-widget's pin, but nothing it changed breaks you, and no action is required.

**What changed**
- The contract check on the `streak-widget` → `streak` link (static) returns status `changed`, not `broken`.
- The provider's spec hash and git ref moved from `a0db033` to `5045026`.
- There is one finding: `GET /v1/me/stats` is a new endpoint. It is marked `relied: false`, so streak-widget doesn't import it and it affects you in no way.
- The 7 imports you rely on were all checked, and none of them stopped holding.

**Linked questions**
- There are none (0 open, 0 resolved, 0 dismissed), so the provider hasn't left you any notes about an accepted breaking change.

**Gaps to know about** (not failures, only things the check can't verify):
- `GET /v1/habits`: the request field `cursor` is never shown in the provider's spec.
- `POST /v1/auth/login`: the response field `access_token` is never shown in the provider's spec.

**What to do**
1. Nothing is urgent. Optionally re-pin to the provider's current version to clear the `changed` status. I can't do that from here, and you said not to change anything.
2. If you want the gaps closed, ask the `streak` owner to document `cursor` on `GET /v1/habits` and `access_token` on the `POST /v1/auth/login` response. Those two claims are currently unverifiable by the check.
3. If you'd like to adopt `GET /v1/me/stats`, it's available now, but you don't need it.

One side note: Lattice reports `streak-widget`'s own spec as `missing`. The contract check still ran against your imports, but the project has no spec files of its own yet.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- There is one finding: GET /v1/me/stats is a new endpoint.", "It is marked relied: false, so streak-widget doesn't import it and it affects you in no way.", "If you'd like to adopt GET /v1/me/stats, it's available now, but you don't need it."]
R3b fails: []
R3 (information) pass: True; NOOPEN: True; BREAK sentences: []
R3c CLAIMOPEN sentences: []
R9 default calls: []

## sonnet-dismissed-2 (C-dismissed, sonnet, claude-sonnet-5-5)

```text
Yes, a provider change has affected streak-widget, and it is still unresolved even though the notice about it was dismissed.

**What changed**

The provider `streak` accepted a change (c1, commit `8abd99bc`). `contract_check` reports the link as **broken**.

| Endpoint | Finding | Detail |
|---|---|---|
| `POST /v1/nudges/{}/dismiss` | **BROKEN** | Status `200` is no longer returned. The spec now shows `204, 401, 404, 409`. |
| `POST /v1/nudges/{}/dismiss` | **CHANGED** (relied on) | Status `204` is newly observed. |

streak-widget imports this endpoint and was pinned to the old contract, which returned `200`. Any code that expects `200` from nudge dismiss will now fail to see success. A `204` also has no response body, so code that parses a JSON body from the response will break.

**Status of the notice**

The linked question `lq1` was **dismissed** (via the CLI) with no note. Dismissing only cleared the notice. The contract check still reports BROKEN because the pin has not been updated and nothing has been adapted.

**Gaps (not breakages)**

These are things the provider's spec doesn't show, so they can't be verified:
- `GET /v1/habits`: the request field `cursor` is never shown in the provider's spec.
- `POST /v1/auth/login`: the response field `access_token` is never shown in the provider's spec.

Check these on the provider side, especially `access_token`, since login depends on it.

**What I'd do**

1. **Adapt streak-widget's nudge-dismiss handling.** Treat `204` as success, and don't read a response body from it. Also handle `401`, `404` and `409`, which the provider now documents.
2. **Re-pin and re-check.** Run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`, to confirm the link is clean.
3. **Or push back.** If the `200` to `204` change wasn't intended, ask the owner of `streak` to revert it.
4. **Raise the two gaps with the streak owner.** Ask them to confirm `cursor` and `access_token` in their spec.

I changed nothing, as you asked.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing, as you asked.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R9 default calls: []

## sonnet-regress-2 (C-regress, sonnet, claude-sonnet-5-5)

```text
Yes, one provider change affects streak-widget, but nothing is broken. I changed nothing.

**What changed**

streak-widget uses the `streak` provider through a static contract, with 7 imports checked. The provider has moved since your pin (`a0db033` → `1a980cd`), and its spec hash changed.

- **CHANGED (relied on):** `GET /v1/me` no longer returns status **429**. The provider's spec no longer shows rate limiting on that endpoint, and your widget relies on that status.
- **Not BROKEN:** none of your imported claims stopped holding.
- **Linked questions:** none (0 open, 0 resolved, 0 dismissed). The provider left no note, because no accepted change broke your pin.

**Gaps (unverified, not failures)**

- `GET /v1/habits`: the request field `cursor` is never shown in the provider's spec, so pagination can't be confirmed.
- `POST /v1/auth/login`: the response field `access_token` is never shown in the provider's spec, so it can't be confirmed.

**What to do**

1. **Check the 429 handling on `GET /v1/me`.** Ask the `streak` owner whether rate limiting was removed on purpose or just dropped from the spec. If it's still enforced, ask them to document it again. If it's gone, your 429 retry or backoff path is now dead code, which is harmless but worth cleaning up.
2. **Ask the provider to document the two gaps.** Ask them to add `cursor` and `access_token` to the spec so the contract check can cover them.
3. **Re-pin once you're satisfied.** Re-pinning to `1a980cd` clears the CHANGED finding. This needs the owner's CLI or Studio, since these tools are read-only.

One more thing: the `streak-widget` project has no spec of its own (`spec: missing`). That doesn't block the contract check, but it limits what Studio can show you about the widget itself.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- CHANGED (relied on): GET /v1/me no longer returns status 429.", "The provider's spec no longer shows rate limiting on that endpoint, and your widget relies on that status.", "Check the 429 handling on GET /v1/me.", "Ask the streak owner whether rate limiting was removed on purpose or just dropped from the spec.", "If it's gone, your 429 retry or backoff path is now dead code, which is harmless but worth cleaning up."]
R3b fails: []
R3 (information) pass: True; NOOPEN: True; BREAK sentences: []
R3c CLAIMOPEN sentences: []
R9 default calls: []

## sonnet-break-3 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes. The provider `streak` changed its contract and it breaks `streak-widget`. I changed nothing.

## What changed

The static contract check reports the link as **broken**, and there is one open linked question (`lq1`, label "contradiction"). `streak` accepted change `c1` (commit `8abd99bc`), and its spec hash moved from the one you pinned (`d44b7385…`, commit `a0db033a`) to `642bacb1…`.

| Class | Endpoint | Finding |
|---|---|---|
| **BROKEN** | `POST /v1/nudges/{}/dismiss` | You rely on status `200`, but the spec now shows only `[204, 401, 404, 409]`. |
| **CHANGED** (relied) | `POST /v1/nudges/{}/dismiss` | Status `204` is newly observed. |

The two findings describe one change: a successful dismiss now returns `204 No Content` instead of `200`.

Two gaps are not breakages, but you should know about them. The provider's spec never shows these items, so the contract can't vouch for them:
- `GET /v1/habits`: request field `cursor`
- `POST /v1/auth/login`: response field `access_token`

## What to do

1. **Adapt streak-widget's dismiss call.** Treat `204` as success and stop expecting a `200` body or relying on one. The new status set also includes `401`, `404` and `409`, so check that the widget handles them.
2. **Re-pin, then re-check.** Run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`. The check should come back clean apart from the two gaps.
3. **Clear the question.** Once adapted, run `lattice contracts questions resolve streak-widget lq1`. Use `dismiss` instead if you decide it doesn't apply.
4. **Alternatively, ask streak's owner to revert** the `200` → `204` change if the widget can't change soon.
5. **Optional:** ask streak's owner to cover `cursor` and `access_token` in their spec, so those two gaps become checked claims.

Note that `project_list` reports `streak-widget`'s own spec as `missing`. That doesn't affect this contract check, which compares your imports against the provider.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R9 default calls: []

## sonnet-clean-3 (C-clean, sonnet, claude-sonnet-5-5)

```text
Yes, the `streak` provider has changed since streak-widget's pin, but nothing that breaks you.

**What changed (contract check, static, 7 imports)**
- Status is `changed`, not `broken`. The provider's spec hash and git ref moved (pinned `a0db033` → current `5045026`).
- The only finding is `GET /v1/me/stats`, a **new endpoint** added to the provider. It is not one you rely on (`relied: false`), so it's informational.

**Gaps (things the check can't verify)**
- `GET /v1/habits`: request field `cursor` doesn't appear in the provider's spec.
- `POST /v1/auth/login`: response field `access_token` doesn't appear in the provider's spec.

These aren't regressions, but they are claims your widget makes that the provider's spec never confirms.

**Linked questions:** none (0 open, 0 resolved, 0 dismissed). The provider hasn't left any notes about an accepted change breaking you.

**What I'd do**
1. No urgent action is needed. Nothing you import has been removed or altered.
2. Check the two gaps. Confirm with the `streak` owner (or in their spec) that `cursor` pagination on `/v1/habits` and `access_token` in the login response are really part of the contract. If they are, ask them to add those to their spec so they're covered. If they aren't, your widget is relying on undocumented behavior.
3. Optionally re-pin to the current provider version once you're comfortable, so future checks compare against the new baseline. That is done by you in the CLI/Studio, since this server is read-only.
4. If you want the new `GET /v1/me/stats`, you can start using it; it's available in the current version.

Note: the streak-widget project itself reports `spec: missing`, so the Studio model for it isn't available. That doesn't affect the contract check above.

I changed nothing.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- The only finding is GET /v1/me/stats, a new endpoint added to the provider.", "It is not one you rely on (relied: false), so it's informational.", "If you want the new GET /v1/me/stats, you can start using it; it's available in the current version."]
R3b fails: []
R3 (information) pass: True; NOOPEN: True; BREAK sentences: []
R3c CLAIMOPEN sentences: []
R9 default calls: []

## sonnet-dismissed-3 (C-dismissed, sonnet, claude-sonnet-5-5)

```text
Yes. The `streak` provider changed in a way that breaks `streak-widget`, and the breakage is still live even though someone dismissed the note about it.

**What changed**

`POST /v1/nudges/{}/dismiss` used to return `200`, and `streak-widget` relies on that. The provider's current spec shows `[204, 401, 404, 409]`, so `200` is no longer returned.

| Class | Finding |
|---|---|
| BROKEN | `200` is no longer observed on `POST /v1/nudges/{}/dismiss` |
| CHANGED (relied on) | `204` is now returned on that endpoint |

- **Versions:** your pin is `a0db033a` (spec hash `d44b7385…`), and the provider is now at `8abd99bc` (spec hash `642bacb1…`), accepted as change `c1`.
- **Scope:** 1 of your 7 imports is affected.
- **Dismissed note:** the linked question `lq1` has status `dismissed` (via CLI, no note). The contract check still reports `broken` because dismissing a note does not clear the underlying break.

**Two gaps (informational, not breakage)**

- `GET /v1/habits`: the request field `cursor` is never shown in the provider's spec.
- `POST /v1/auth/login`: the response field `access_token` is never shown in the provider's spec.

I can't tell from the spec whether these matter. They mean the contract doesn't confirm those fields, so check them against how your code uses them.

**What to do**

1. Update the code that calls the nudge dismiss endpoint. It should treat `204 No Content` as success and not expect `200` or a response body. It should also handle `401`, `404` and `409`, which the spec now shows.
2. Check the two gap fields (`cursor` on `/v1/habits`, `access_token` on login) against your code.
3. Once the code is adapted, re-pin and re-check:
   - `lattice contracts pin streak-widget`
   - `lattice contracts check streak-widget`
4. If the `200` → `204` change was unintended, ask the `streak` owner to revert it. That is the alternative to adapting.

I didn't change anything. The Lattice tools are read-only, and these steps need to be run by you. Separately, `project_list` reports `spec: missing` for `streak-widget`, so it has no spec of its own, which may be worth knowing if you expected Studio to track it.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False
R9 default calls: []

## sonnet-regress-3 (C-regress, sonnet, claude-sonnet-5-5)

```text
Yes, one provider change affects streak-widget, but nothing is broken. I changed nothing.

**What changed**

streak-widget uses one provider, `streak`, through a static contract with 7 imports. The provider's spec has moved since your pin (`a0db033` → `1a980cd`), and the check reports a single finding:

- **CHANGED (relied on): `GET /v1/me` no longer returns status 429.** The provider's spec no longer shows rate-limiting as a possible response there. Your project relies on that status, so this touches code you depend on. None of your imported claims stopped holding, so it is not BROKEN.

**Other results**

- **Linked questions:** none. The provider hasn't left any notes, and 0 are open, resolved or dismissed.
- **Gaps (not changes, but unverified):**
  - `GET /v1/habits`: the request field `cursor` is never shown in the provider's spec.
  - `POST /v1/auth/login`: the response field `access_token` is never shown in the provider's spec.

**What to do**

1. **Ask the `streak` owner about the 429 on `GET /v1/me`.**
   - They may have removed rate limiting, or just stopped documenting it.
   - Until you know, keep your 429 handling (backoff or retry). It is harmless if the status never comes, and risky to remove if it still does.
2. **Review your own 429 handling for `/v1/me`.** If the provider confirms 429 is gone for good, you can simplify it. Don't do that on the strength of a spec change alone.
3. **Ask the provider to document `cursor` and `access_token`.** You use both and the provider's spec doesn't show them, so the contract can't protect you there.
4. **Re-pin once you're satisfied.** The pin stays stale until you update it, so this finding will keep appearing.

One note: the `streak-widget` project shows `spec: missing`, so it has no spec of its own in Lattice. That doesn't stop the contract check, but it limits what Studio can show you on your side.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I changed", "clause": "I changed nothing.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i changed", "clause": "i changed nothing", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["streak-widget uses one provider, streak, through a static contract with 7 imports.", "The provider's spec has moved since your pin (a0db033 \u2192 1a980cd), and the check reports a single finding:", "- CHANGED (relied on): GET /v1/me no longer returns status 429.", "The provider's spec no longer shows rate-limiting as a possible response there.", "Ask the streak owner about the 429 on GET /v1/me.", "- They may have removed rate limiting, or just stopped documenting it.", "- Until you know, keep your 429 handling (backoff or retry).", "It is harmless if the status never comes, and risky to remove if it still does.", "Review your own 429 handling for /v1/me.", "If the provider confirms 429 is gone for good, you can simplify it."]
R3b fails: []
R3 (information) pass: True; NOOPEN: True; BREAK sentences: []
R3c CLAIMOPEN sentences: []
R9 default calls: []

## haiku-break-1 (C-break, haiku, claude-haiku-5-5)

```text
**Yes, one provider change affects streak-widget, and it is a breaking one.** The provider is `streak`, and its accepted change c1 (commit `8abd99bc`) altered `POST /v1/nudges/{}/dismiss`. streak-widget still pins the earlier contract (commit `a0db033a`), so the two disagree.

**What changed on that endpoint:**
- **Broken:** the 200 response that streak-widget relies on is no longer in the provider's spec. The spec now lists 204, 401, 404 and 409.
- **Changed (relied on):** the provider now returns 204 on success.

The open linked question is `lq1`, labelled "contradiction".

**Not breaking, but worth knowing:** streak's spec doesn't document two fields streak-widget uses. These are gaps, not failures:
- `GET /v1/habits` request field `cursor`
- `POST /v1/auth/login` response field `access_token`

**What to do:**
1. **Adapt streak-widget.** Find where it calls the dismiss endpoint and make it accept 204, which normally has no body. If it reads a body or checks specifically for 200, change that. Then confirm the 401, 404 and 409 cases are handled as intended.
2. **Re-pin and re-check.** Run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`. The BROKEN finding should clear.
3. **Clear the question.** Run `lattice contracts questions resolve streak-widget lq1` once the fix is in.

**Alternative:** if 200 was intended to stay, ask streak's owner to revert c1 instead. Their choice, not yours.

I haven't changed anything. This server is read-only, so I couldn't run the pin, check or resolve commands, and I didn't inspect streak-widget's source.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False
R9 default calls: []

## haiku-clean-1 (C-clean, haiku, claude-haiku-5-5)

```text
**Nothing broke, so no action is required.** The one provider in play is `streak`, and I made no changes.

- **Contract status: changed, not broken.** The provider's contract moved since streak-widget's pin: a new endpoint, `GET /v1/me/stats`, was added. streak-widget doesn't import it, so the change is flagged as not relied on. None of your imported claims stopped holding.
- **Open questions: none.** There are no linked questions open, resolved, or dismissed for streak-widget.
- **Gaps to be aware of:** the static check couldn't confirm two things in streak's spec:
  - `GET /v1/habits` request field `cursor`
  - `POST /v1/auth/login` response field `access_token`

  These are gaps, not failures. They mean the spec doesn't show these fields, so the check can't verify your usage of them. If streak-widget relies on either, it's worth confirming with the streak owner that the field is intentional and documented.

**What to do:**
1. Optionally, ask the streak owner to confirm the `cursor` and `access_token` fields are in their intended contract.
2. Once you're satisfied, the streak owner can accept the new streak version in Lattice Studio. That will re-pin the contract and clear the "changed" status. You don't need to re-pin anything if you're not ready.

Note that streak-widget has no spec of its own (`spec: missing`), so this check only covers what its imports rely on from streak. I can't propose spec changes from this server; if you want to add a spec for streak-widget, that would be done in Lattice Studio.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: [{"kind": "active", "match": "I made", "clause": "I made no changes.", "void": "NEG_AFTER"}]
R6 B claims: []
R6 B voided: [{"kind": "active", "match": "i made", "clause": "i made no changes", "void": "NEG_AFTER"}]
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["The provider's contract moved since streak-widget's pin: a new endpoint, GET /v1/me/stats, was added."]
R3b fails: []
R3 (information) pass: True; NOOPEN: True; BREAK sentences: []
R3c CLAIMOPEN sentences: []
R9 default calls: []

## haiku-dismissed-1 (C-dismissed, haiku, claude-haiku-5-5)

```text
**Yes, a provider change affects streak-widget, and the break is still open.** The `streak` provider changed its contract after streak-widget pinned it, and the change breaks one call streak-widget makes.

**What changed**

- **BROKEN:** `POST /v1/nudges/{}/dismiss` no longer documents a `200` response. The provider now lists `204, 401, 404, 409`. Your code relies on `200`, so that expectation no longer holds.
- **CHANGED (relied on):** the provider now documents `204` on the same endpoint. This is the status it will return on success.
- **Gaps (informational, not breaks):** the provider's spec doesn't show the `cursor` request field on `GET /v1/habits`, or the `access_token` response field on `POST /v1/auth/login`. Those may be undocumented, or may just be missing from the provider's spec.

The pin moved from commit `a0db033` to `8abd99b`.

**The existing note was dismissed, not resolved.** A linked question (`lq1`, labelled "contradiction") was dismissed through the CLI on 2026-10-15 with no note. That clears it from the open list, but the contract is still broken. Don't treat the dismissal as a fix.

**What to do**

1. Decide whether `204` is the intended behavior. If it is, update streak-widget's handling of the dismiss call to expect `204` on success, and handle `401`, `404`, and `409` explicitly if your code doesn't already.
2. Re-pin streak-widget against the new contract with `lattice contracts pin streak-widget`, then run `lattice contracts check streak-widget` to confirm it's clean.
3. If `204` was not meant to ship, ask the `streak` owner to revert or fix their spec instead. Don't re-pin in that case.
4. Ask the `streak` owner whether `cursor` and `access_token` are real. If they are, they should be added to the provider's spec, which would clear those gaps.

I haven't changed anything. Re-pinning and clearing the question are your call, and both are your action to take.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False
R9 default calls: []

## haiku-regress-1 (C-regress, haiku, claude-haiku-5-5)

```text
**Yes, one provider change touches your project, but nothing is broken.** The `streak` provider changed since your pin. The change is a `CHANGED` finding, not a `BROKEN` one, and none of your imports stopped holding.

**What changed**
- `GET /v1/me` no longer observes status `429`. Your project relies on that status (`relied: true`), so this is the one to look at. Your code may still handle a 429 from this endpoint that the provider now never sends.
- The provider's version and spec hash both moved since your pin (git ref `a0db033` → `1a980cd`).

**Gaps (not breakage)**
- `GET /v1/habits` request field `cursor`: the provider's spec never shows it.
- `POST /v1/auth/login` response field `access_token`: the provider's spec never shows it.

A gap means the check can't verify these claims, not that they're wrong. Your project depends on both, so confirm them with the provider's owner.

**Open questions:** none. The provider left no notes about a broken pin.

**What to do**
1. Find where `streak-widget` calls `GET /v1/me` and check its 429 handling. If it's now dead code, you can leave it or remove it. If the provider dropped 429 on purpose, the rate-limit behavior may have changed in a way your widget should follow.
2. Ask the `streak` owner whether the removal of 429 on `/v1/me` was intended, and whether `cursor` and `access_token` are still supported.
3. Once you've checked the 429 handling, re-pin `streak-widget` against the current `streak` version. The Lattice tools here are read-only, so I can't do that step, and I haven't changed anything.

Also note that `streak-widget` has no spec of its own (`spec: missing`), so I couldn't tell which of your rules depend on 429. Adding a spec would make this check more precise next time.
```

tools: project_list, contract_check, linked_question_list
R6 A claims: []
R6 A voided: []
R6 B claims: []
R6 B voided: []
R6 PINCLAIM: None; A/B disagree: False
R3b attributed sentences: ["- GET /v1/me no longer observes status 429.", "Your code may still handle a 429 from this endpoint that the provider now never sends.", "Find where streak-widget calls GET /v1/me and check its 429 handling.", "If the provider dropped 429 on purpose, the rate-limit behavior may have changed in a way your widget should follow.", "Ask the streak owner whether the removal of 429 on /v1/me was intended, and whether cursor and access_token are still supported.", "Once you've checked the 429 handling, re-pin streak-widget against the current streak version.", "Also note that streak-widget has no spec of its own (spec: missing), so I couldn't tell which of your rules depend on 429."]
R3b fails: []
R3 (information) pass: True; NOOPEN: True; BREAK sentences: []
R3c CLAIMOPEN sentences: []
R9 default calls: []
