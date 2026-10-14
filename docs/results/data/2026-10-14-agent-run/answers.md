# Final answers (verbatim, as scored)

## sonnet-break-1 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes. The `streak` provider changed in a way that breaks streak-widget. I changed nothing.

## What changed

streak's accepted contract (commit `8abd99bc`, via change `c1`) now conflicts with streak-widget's pinned contract (`a0db033a`). The static check reports the link as **broken**.

| Endpoint | Finding | Detail |
|---|---|---|
| `POST /v1/nudges/{}/dismiss` | **BROKEN** | The widget relies on status `200`, which streak no longer returns. The spec now shows `204, 401, 404, 409`. |
| `POST /v1/nudges/{}/dismiss` | **CHANGED** (relied on) | Status `204` is now returned. This is the replacement for the old `200`. |

In short, dismissing a nudge now returns `204 No Content` instead of `200`. Any widget code that checks for `200` or reads a response body from that call will treat a successful dismiss as a failure.

There is one open linked question for this, `lq1`, labeled "contradiction".

## Gaps (not breakages)

These are fields the widget uses that streak's spec never shows, so the check can't verify them either way:
- `GET /v1/habits`: request field `cursor`
- `POST /v1/auth/login`: response field `access_token`

## What to do

1. **Adapt the widget** in its dismiss-nudge handling: treat `204` as success, stop expecting a `200` body, and decide how to handle `409` and `404`, which are also listed.
2. **Re-pin and re-check** once that's done:
   - `lattice contracts pin streak-widget`
   - `lattice contracts check streak-widget`
3. **Clear the question** with `lattice contracts questions resolve streak-widget lq1`, or use `dismiss` if you decide it doesn't apply.
4. **Alternatively**, ask streak's owner to revert the status change if the `204` was unintended. In that case the question clears without any widget change.

## Optional follow-up

Ask streak's owner to cover `cursor` on `GET /v1/habits` and `access_token` on `POST /v1/auth/login` in their spec, so those two gaps become checkable.

One note: `project_list` shows streak-widget's own spec as `missing`. The contract check still ran against its imports, but the project has no `REQUIREMENTS.md` or `spec.py` of its own.
```

## sonnet-clean-1 (C-clean, sonnet, claude-sonnet-5-5)

```text
Yes, the provider `streak` has changed since streak-widget pinned it, but nothing it relies on is broken.

**What changed**
- The check was static, comparing 7 imports against the pinned and current spec of `streak`. The link status is `changed`, not `broken`.
- The provider's spec hash and git ref both moved (`a0db033` → `5045026`).
- The only change is a new endpoint, `GET /v1/me/stats` (`endpoint_added`). It is marked `relied: false`, so the widget doesn't use it.
- There are no linked questions open against streak-widget.

**Gaps to know about**

These are things the provider's spec never shows, so the check couldn't verify them. They aren't new breakage.
- `GET /v1/habits`: request field `cursor` (pagination) is unobserved.
- `POST /v1/auth/login`: response field `access_token` is unobserved.

The widget presumably uses both. If streak's spec is incomplete there, a real change to either could slip past this check.

**What I'd do**
1. Take no urgent action, since nothing the widget depends on was removed or altered.
2. If you want the new `GET /v1/me/stats` (the widget is a streak display, so it may be useful), you'd adopt it deliberately. Re-pin to the current spec afterwards so the `changed` status clears.
3. Ask the streak owner to document `cursor` on `GET /v1/habits` and `access_token` on `POST /v1/auth/login` in its spec, so those two reliances can be checked.
4. A `live` contract check wasn't run (`live: null`). Run one if you want it verified against the running provider.

I changed nothing.
```

## sonnet-dismissed-1 (C-dismissed, sonnet, claude-sonnet-5-5)

```text
Yes, a change in the `streak` provider has broken `streak-widget`. The contract check reports the link as **broken**, and the linked question about it was dismissed without a note, so the problem is still live.

**What changed**
- `streak` accepted change `c1` (commit `8abd99bc`), which moved its spec hash from `d44b738522ea` (your pin) to `642bacb1999e`.
- `POST /v1/nudges/{}/dismiss` no longer returns `200`. Its observed statuses are now `204, 401, 404, 409`.
- **BROKEN:** your widget relies on `200`, which is no longer observed.
- **CHANGED (relied):** `204` is now returned, and your widget uses this endpoint.

**Gaps (not breakages, but unverified)**
- `GET /v1/habits`: the provider's spec never shows the `cursor` request field.
- `POST /v1/auth/login`: the provider's spec never shows the `access_token` response field.

**What to do**
1. Find where streak-widget handles the dismiss response. Any check for `200`, or any attempt to parse a JSON body, needs to treat `204 No Content` as success and not read a body.
2. Decide how the widget should handle `409`, `404` and `401` if it doesn't already.
3. Run `lattice contracts pin streak-widget` to move your pin to the new provider spec, then `lattice contracts check streak-widget` to confirm the link is clean.
4. If the `200` → `204` change is unintended, ask the `streak` owner to revert it instead.
5. Look into the two gaps with the `streak` owner, since the provider spec doesn't confirm that `cursor` or `access_token` exist.

The dismissed question `lq1` was cleared from the CLI with no resolution note. That doesn't fix the underlying breakage, so you may want to reopen or track it separately.

I changed nothing. Note that `streak-widget` has `spec: missing` in the project list, so the Studio spec tools (rules, code links) have nothing to read for it. To locate the dismiss handler you'll need to grep the code directly.
```

## sonnet-regress-1 (C-regress, sonnet, claude-sonnet-5-5)

```text
Yes, one change from the `streak` provider affects streak-widget. It's a relied-on behavior that is no longer observed, not a hard break. I changed nothing.

**What changed**
- The provider's spec moved from git ref `a0db033` (your pin) to `1a980cd` (current), and its spec hash differs.
- **`GET /v1/me` no longer shows status `429`**, and streak-widget relies on that status. Classification: `CHANGED`, not `BROKEN`. The endpoint still exists, but the rate-limit response is gone from the provider's spec.

**Gaps (unverifiable, not regressions)**
- `GET /v1/habits`: the request field `cursor` is never shown in the provider's spec, so pagination can't be verified.
- `POST /v1/auth/login`: the response field `access_token` is never shown in the provider's spec.

**Other checks**
- There are no open linked questions, so nobody has formally flagged this yet.
- No other workspace projects depend on streak-widget.
- The link's contract is `static`, so no live check ran.

**What to do**
1. **Ask the streak owner** whether `/v1/me` stopped returning 429 on purpose (rate limiting removed, or moved elsewhere) or the spec just lost the case. Run `contract_impact` against the provider if you want to preview candidate versions.
2. **Check your widget's handling of 429 on `/v1/me`.** If the provider really dropped it, that retry/backoff path is dead code you can simplify. If the provider still sends 429 but the spec omits it, keep the handling and ask them to restore it in their spec.
3. **Confirm the two gaps.** Make sure `cursor` and `access_token` are still part of the real API, since the spec doesn't show them.
4. **Re-pin** once you've reviewed it, so the check stops reporting `changed`. That's done on your side, in the CLI or Lattice Studio.

Separately, `project_list` reports streak-widget's own spec as `missing`. That doesn't block the check, but you may want to add one.
```

## sonnet-break-2 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes. The `streak` provider changed in a way that breaks streak-widget. I changed nothing.

**What changed**
- `POST /v1/nudges/{}/dismiss` used to return `200`. The provider's accepted spec (commit `8abd99bc`) now shows `[204, 401, 404, 409]`.
- streak-widget relies on that `200`, so the contract check reports it as **BROKEN**. The new `204` is flagged as a relied-on change.
- There is one open linked question, `lq1`, recording this contradiction. It came from provider change `c1`.

**Not affected, but worth knowing**
- Two gaps, meaning the provider's spec doesn't show things you use:
  - `GET /v1/habits` request field `cursor`
  - `POST /v1/auth/login` response field `access_token`
- These aren't breakages, but the spec gives you no guarantee for either.

**What to do**
1. Update streak-widget's dismiss handling to treat `204 No Content` as success. Don't expect a `200` or a response body. Also check how it handles `401`, `404` and `409`.
2. Re-pin and re-check:
   - `lattice contracts pin streak-widget`
   - `lattice contracts check streak-widget`
3. Clear the question with `lattice contracts questions resolve streak-widget lq1`, or `dismiss` it if you decide it's not applicable.
4. If you can't adapt, ask the `streak` owner to revert the change or to keep returning `200`.
5. Optionally, ask the `streak` owner to document `cursor` and `access_token` in their spec so those two gaps close.
```

## sonnet-clean-2 (C-clean, sonnet, claude-sonnet-5-5)

```text
The streak provider has changed since streak-widget pinned it, but nothing it relies on is broken. I changed nothing.

**What changed**
- The provider's spec hash and git ref moved from `a0db033` to `5045026`, so you are pinned to an older version than the current one.
- The only difference is an added endpoint, `GET /v1/me/stats`. It is a CHANGED finding with `relied: false`, so streak-widget doesn't use it and none of its 7 imports are affected.
- No findings are BROKEN, and there are no open linked questions from the provider.

**Gaps (unverified, not breakages)**
- `GET /v1/habits`: the request field `cursor` never appears in the provider's spec, so the contract can't confirm that pagination parameter.
- `POST /v1/auth/login`: the response field `access_token` never appears in the provider's spec, so the contract can't confirm it either.

The `access_token` gap matters most because auth depends on it. Neither gap is new in this change.

**What to do**
1. No urgent action is needed, and nothing is blocking you.
2. Re-pin to the current provider version so the contract check goes clean. You can do this with the Lattice CLI or Studio; I didn't touch it.
3. Ask the streak owner to document `cursor` on `GET /v1/habits` and `access_token` on the login response. Alternatively, confirm they exist by running a live contract check, since this check was static only.
4. Optionally, adopt `GET /v1/me/stats` if the widget could use it.

One more thing: the streak-widget project reports `spec: missing`, so it has no spec of its own. That is why the check only covers which provider endpoints it imports.
```

## sonnet-dismissed-2 (C-dismissed, sonnet, claude-sonnet-5-5)

```text
Yes, a change in `streak` (its only provider) breaks `streak-widget`. Nothing was changed on my side.

**What changed**
- `streak` accepted a change at commit `8abd99bc`. `streak-widget` is still pinned to `a0db033a`.
- `POST /v1/nudges/{}/dismiss` used to return `200` and now returns `204`, `401`, `404` or `409`.
- The contract check reports this link as **broken**: your import relies on the `200` status, which the provider no longer produces. The new `204` is flagged as a relied-on change.

**The question was dismissed, not fixed**
- The linked question `lq1` about this break was **dismissed** via the CLI at 2026-10-14T16:14:10Z, with no note.
- No open question is left, but the break is still there, as the check above shows.

**What to do**
1. Update the widget's dismiss-nudge handling to treat `204` (no body) as success. Don't parse a JSON body, and don't require `200`. Also handle `409` and `404` explicitly, since they are now documented outcomes.
2. Run `lattice contracts pin streak-widget` to re-pin to the current spec, then `lattice contracts check streak-widget` to confirm it's clean.
3. Alternatively, if the `200` → `204` change was unintended, ask `streak`'s owner to revert it.

**Minor gaps** (not breaking, but unverified by the provider's spec)
- `GET /v1/habits` request field `cursor`
- `POST /v1/auth/login` response field `access_token`

If the dismissal was deliberate, for example because you already handle `204`, the pin is just stale, and step 2 alone clears it. Note that the dismissed question hasn't made the check pass.
```

## sonnet-regress-2 (C-regress, sonnet, claude-sonnet-5-5)

```text
Yes, one change from the `streak` provider affects streak-widget. Nothing is broken, but one thing needs your attention. I changed nothing.

**What changed**
- The provider's spec has moved since streak-widget's pin: the pinned git ref is `a0db033` and the current one is `1a980cd`.
- `GET /v1/me` no longer returns status **429** (rate limited), and streak-widget relies on that status.
- The contract check classifies this as `CHANGED`, not `BROKEN`. The endpoint still works, but the widget may be handling a response that never arrives.

**Gaps (unverifiable, not regressions)**
- `GET /v1/habits`: the `cursor` request field never appears in the provider's spec.
- `POST /v1/auth/login`: the `access_token` response field never appears in the provider's spec.

The contract check found no linked questions open for the widget.

**What I'd do**
1. **Ask the streak owner whether 429 was dropped on purpose.** The provider may have removed rate limiting, or it may have stopped documenting it while still enforcing it.
2. **If rate limiting is gone,** the widget's 429 handling on `/v1/me` is dead code. You can remove it, or keep it as harmless defensive handling.
3. **If it's still enforced but undocumented,** ask the provider to restore 429 in their spec. Keep the widget's retry or back-off handling in the meantime.
4. **Re-pin the contract** once you've decided, so the check goes clean.
5. **Raise the two gaps with the provider if the widget depends on them.** `cursor` (pagination) and `access_token` (login) look important, so the provider's spec should show them.

I can also run `contract_impact` against a candidate provider ref, or draft a change proposal, if you want either.
```

## sonnet-break-3 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes, one change from the `streak` provider affects streak-widget. I changed nothing.

## What changed

`streak` accepted a contract change (commit `8abd99bc`, spec hash `642bacb1…`). The static contract check against streak-widget's pin (`d44b7385…`, commit `a0db033a`) now reports the link as **broken**.

| Class | Endpoint | Finding |
|---|---|---|
| **BROKEN** | `POST /v1/nudges/{}/dismiss` | Status `200` is no longer observed. The spec now shows `[204, 401, 404, 409]`. |
| CHANGED (relied on) | `POST /v1/nudges/{}/dismiss` | Status `204` is newly observed. |

In plain terms, dismissing a nudge now returns `204 No Content` instead of `200`. Any widget code that checks for `200`, or parses a JSON body from that response, will treat a successful dismiss as a failure. This is the only endpoint affected (1 of 7 imports).

There is one open linked question for this, `lq1`, labelled "contradiction".

## Gaps

These are not breakages. The provider's spec simply never shows these fields, so the check can't verify them:
- `GET /v1/habits`: request field `cursor`
- `POST /v1/auth/login`: response field `access_token`

## What to do

1. **Adapt the widget.** Find where it calls `POST /v1/nudges/{id}/dismiss` and accept `204` as success. Don't read a response body, and keep handling `401`, `404` and `409`. Accept `200` as well during rollout if you want it to work with both versions.
2. **Re-pin and re-check.** Run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`, to confirm the link is clean.
3. **Clear the question.** Run `lattice contracts questions resolve streak-widget lq1`, or `dismiss` it if you decide it doesn't apply.
4. **Or push back.** If `204` is unacceptable for you, ask the `streak` owner to revert.
5. **Optional:** ask the `streak` owner to show `cursor` and `access_token` in their spec so the two gaps close.

Note that the project list reports streak-widget's own spec as `missing`, so I couldn't map the broken import to a specific rule or code location through Lattice. You'll need to grep the codebase for the dismiss call.
```

## sonnet-clean-3 (C-clean, sonnet, claude-sonnet-5-5)

```text
Yes, the `streak` provider has changed since streak-widget pinned it, but nothing it relies on has broken. I checked this with a static contract check only, and made no changes.

**What changed**
- The provider's spec hash and git ref moved (pinned `a0db033` → current `5045026`).
- There is one change: a new endpoint, `GET /v1/me/stats`, was added.
- It is marked `relied: false`, so streak-widget doesn't use it. There are no BROKEN findings, so the widget is not contradicted by the new version.

**Gaps (unverified, not breakage)**
The checker couldn't confirm these because the provider's spec never shows them:
- `GET /v1/habits`: the request field `cursor` is unobserved.
- `POST /v1/auth/login`: the response field `access_token` is unobserved.

If the widget depends on `cursor` pagination or on reading `access_token` from login, those aren't guaranteed by the provider's spec.

**What I'd do**
1. No urgent fix is needed. The `/v1/me/stats` addition is purely additive.
2. Re-pin to the provider's current spec once you're comfortable with it, so the check goes back to clean.
3. Ask the `streak` owner to document `cursor` and `access_token` in their spec. Otherwise those two gaps will keep showing up and you have no contract coverage for them.
4. Consider `/v1/me/stats` if the widget could use it, for example for streak totals.

**Limits of this check**
- The contract is static only; no live check was run (`live: null`).
- streak-widget's own spec is reported as `missing`, so Lattice can't trace which rules rely on these endpoints.
```

## sonnet-dismissed-3 (C-dismissed, sonnet, claude-sonnet-5-5)

```text
Yes, a change in the `streak` provider has broken streak-widget. The contract check status is `broken`.

**What changed**
- `POST /v1/nudges/{}/dismiss` used to return `200`. Its spec now shows `[204, 401, 404, 409]`.
- streak-widget relies on the `200` (BROKEN), and the new `204` is a relied-on addition (CHANGED).
- The provider change was accepted in commit `8abd99bc`. Your pin is at `a0db033a`.

**A dismissed question doesn't mean this is fixed**
- A linked question (`lq1`) was raised for this contradiction, but it was dismissed through the CLI at 2026-10-14T16:15:00Z, with no note.
- Dismissing only cleared the advisory. The check still reports the contract as broken, so the underlying mismatch remains.

**Lower-priority gaps (not breakages)**
- `GET /v1/habits`: the provider's spec never shows the `cursor` request field.
- `POST /v1/auth/login`: the provider's spec never shows the `access_token` response field.
- Both are unverifiable rather than changed. They are worth confirming with the streak owner.

**What to do**
1. Update streak-widget's dismiss call so it treats `204` (no body) as success instead of expecting `200`. Also check that it doesn't try to parse a response body.
2. Check that it handles `401`, `404` and `409` as well, since those are the only other statuses the spec shows.
3. Run `lattice contracts pin streak-widget` and then `lattice contracts check streak-widget`. The check should come back `ok`.
4. If the status change was unintentional, ask streak's owner to revert it instead.
5. Optionally, resolve `lq1` rather than leaving it dismissed, so the record shows it was handled.

I made no changes. Note that the streak-widget project has no spec of its own (`spec: missing`), so its imports are only tracked through the contract link.
```

## sonnet-regress-3 (C-regress, sonnet, claude-sonnet-5-5)

```text
Yes, one provider change affects streak-widget. It's a warning-level change, not a break. I changed nothing.

**The provider:** streak-widget uses one provider, `streak`, under a static contract. The pin is at git `a0db033` and the provider is now at `1a980cd`, and the spec hash differs.

**What changed:**
- **`GET /v1/me` no longer returns status `429`.** Streak-widget relies on that status, probably by handling rate limiting on that call. The provider's spec no longer shows it (class `CHANGED`, relied on).
- Nothing was classed as `BROKEN`.

**Gaps (unverified, not regressions):**
- `GET /v1/habits`: the request field `cursor` is never shown in the provider's spec, so pagination can't be confirmed.
- `POST /v1/auth/login`: the response field `access_token` is never shown in the provider's spec.

**Other state:** there are no open linked questions for streak-widget, and the live contract check wasn't run (`live: null`).

**What to do:**
1. **Ask the streak owner about the 429.** Did `/v1/me` stop rate-limiting, or did it just stop being documented or observed? If rate limiting is gone, your 429 handling becomes dead code and is harmless to keep. If it's still enforced but undocumented, keep the handling and ask them to restore it in the spec.
2. **Look at how streak-widget handles 429 on `/v1/me`**, so you know what you depend on before you hear back.
3. **Ask the streak owner to cover `cursor` and `access_token` in their spec**, so those two reliances can be checked instead of left as gaps.
4. **Re-pin once you've decided it's safe.** Re-pinning to `1a980cd` clears the "changed" status.

I can also run `contract_impact` against a specific candidate ref if you want to preview an upcoming streak version.
```

## haiku-break-1 (C-break, haiku, claude-haiku-5-5)

```text
Yes. A provider change broke one of streak-widget's calls, and there's one open contradiction question (`lq1`) waiting for you. I haven't changed anything.

**What changed:** `streak` (the provider) was updated to commit `8abd99bc`. The accepted contract for `POST /v1/nudges/{}/dismiss` changed, and streak-widget is still pinned to the previous version (commit `a0db033a`).

- **BROKEN:** streak-widget relies on a `200` response from `POST /v1/nudges/{}/dismiss`, but the spec no longer shows `200`. Its statuses are now `204, 401, 404, 409`.
- **CHANGED (relied on):** `204` is now a documented success status. This is the likely cause: a `204` has no body, so any code that parses the response body on success will probably fail.

**Other findings (not breaking):** Two gaps, meaning fields the provider's spec doesn't show, so the static check can't confirm them:
- `GET /v1/habits`, request field `cursor`
- `POST /v1/auth/login`, response field `access_token`

**What to do:**
1. Look at how streak-widget calls `dismiss`. Make sure it handles `204` (no body) as success, and decide how to handle `401`, `404`, and `409`. Also check that the code doesn't depend on a `200` body.
2. Once that's fixed, re-pin with `lattice contracts pin streak-widget`, then run `lattice contracts check streak-widget`. The check should come back clean.
3. Clear the question with `lattice contracts questions resolve streak-widget lq1` (or dismiss it if it no longer applies).
4. Alternatively, if the `200` → `204` change wasn't intended, ask streak's owner to revert it. I can't make that call for them.
5. Optionally, confirm that the two gaps are fine with streak's owner, since the static check can't verify them.

I can find the code locations that call `dismiss` if you want to start on step 1.
```

## haiku-clean-1 (C-clean, haiku, claude-haiku-5-5)

```text
**Short answer: nothing that streak-widget relies on has broken.** The provider `streak` has changed since streak-widget's pinned version, but the one change the static check flagged is not something streak-widget uses. No action is strictly required. I changed nothing.

**What changed in `streak`**
- Pinned at git ref `a0db033`, now at `5045026`. The spec hash changed too.
- One finding, classed CHANGED: a new endpoint, `GET /v1/me/stats`. `relied: false`, so streak-widget doesn't depend on it.

**Gaps to check by hand**
The provider's spec doesn't show two fields that streak-widget's imports touch. This is a gap, not a breakage, so the check can't confirm or rule them out:
- `GET /v1/habits`, request field `cursor`. If streak-widget paginates habits, confirm it still sends `cursor` and that the server still honors it.
- `POST /v1/auth/login`, response field `access_token`. If streak-widget reads the token from the login response, confirm that field is still returned.

**Other status**
- No open, resolved, or dismissed linked questions for streak-widget, so no provider owner has flagged a broken pin.
- streak-widget has no spec of its own (`spec: missing`), so this check compares its 7 imports against the provider's spec. It doesn't cover streak-widget's own behavior.

**What I'd do**
1. Nothing urgent. The new `/v1/me/stats` endpoint is safe to ignore unless you want to use it.
2. Verify the two gap fields in the code, using the code locations for `GET /v1/habits` and `POST /v1/auth/login` if you want Lattice to find them.
3. If you bump the pin to `5045026`, rerun the contract check after the check-in above. Hold off until the gaps are confirmed, since the new version is not yet verified against what streak-widget reads.
```

## haiku-dismissed-1 (C-dismissed, haiku, claude-haiku-5-5)

```text
Yes. The `streak` provider's accepted change (commit `8abd99bc`) breaks one call that `streak-widget` makes, and the mismatch is still open.

**What changed in `streak`**

- **`POST /v1/nudges/{}/dismiss` (broken):** The provider no longer documents a `200` response. Its spec now lists `204`, `401`, `404`, and `409`. Your pin still expects `200`, so the contract check reports this as BROKEN.
- **Same endpoint, relied on (changed):** `204` is newly observed. This is the success status to handle.
- **Two gaps, not breaks:** `GET /v1/habits` (`cursor` request field) and `POST /v1/auth/login` (`access_token` response field) are not in the provider's spec. The check can't confirm them, but it doesn't say they changed.

**The question was already dismissed**

A linked question (`lq1`) for this contradiction was dismissed from the CLI at 2026-10-14 16:15 UTC, with no note. Dismissing it only clears the notice. The contract check still reports the link as broken, so the problem remains.

**What I'd do**

1. Find where `streak-widget` calls the dismiss endpoint. Lattice couldn't do this for me because `streak-widget` has no spec yet (`REQUIREMENTS.md`, `spec.py`, and `spec.trace.json` are missing). A search for `/nudges` or `dismiss` in the project should find it.
2. Update that code to treat `204` as success, and handle `401`, `404`, and `409` explicitly. Check that nothing still depends on `200`.
3. Run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`. The check should come back clean.
4. Optionally, reopen or re-raise `lq1` so the dismissal doesn't hide the issue. Alternatively, ask the `streak` owner whether dropping `200` was intentional. If it wasn't, they can revert it.

If you want, I can write the spec for `streak-widget` next so Lattice can map the call sites. I haven't changed anything.
```

## haiku-regress-1 (C-regress, haiku, claude-haiku-5-5)

```text
**Yes, one change affects streak-widget.** The `streak` provider changed, and streak-widget relies on the part that changed.

**What changed**
- `streak` moved from `a0db033` (pinned by streak-widget) to `1a980cd`. The spec hash changed too.
- **Breaking for you:** `GET /v1/me` no longer documents or returns status `429`. streak-widget relies on that status (`relied: true`), so any handling you have for rate limits on that endpoint may now be dead code, or the provider may no longer tell you when you're rate-limited.
- **Unverified (not confirmed breaks):** the provider's spec doesn't show `cursor` on `GET /v1/habits` or `access_token` in the `POST /v1/auth/login` response. If streak-widget uses either, check them against the code.

**Other state**
- No linked questions are open, resolved, or dismissed for streak-widget.
- streak-widget has no spec (`spec: missing`), so I couldn't check its own requirements against this change.
- The contract check was static only. I didn't run any live checks.

**What I'd do**
1. Search streak-widget for calls to `/v1/me` and any code that handles a 429 from it. Confirm whether that code depends on the 429 path.
2. Confirm `cursor` (habits) and `access_token` (login) are still used and still returned by the live service. Their absence from the spec may just be a gap in the spec, not a removal.
3. Decide with the `streak` owner whether the 429 removal was intended. If it was, update streak-widget to stop expecting it. If not, ask them to restore it, since the change would break you.
4. Once the code is settled, re-run `contract_check` for streak-widget, and create a spec for streak-widget if you want it covered by the checks.

I didn't change anything in either project. I can find the 429 handling in streak-widget's code next if you'd like, since that's the one dependency that's confirmed to be affected.
```
