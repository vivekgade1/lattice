# Final answers (verbatim, as scored)

## sonnet-break-1 (C-break, sonnet, claude-sonnet-5-5)

```text
Yes. The `streak` provider changed in a way that breaks one of streak-widget's imports. I changed nothing.

## What changed

The static contract check on the `streak-widget` → `streak` link comes back **broken**. `streak` accepted change `c1` (commit `8abd99bc`), and its spec hash moved from the one streak-widget pinned (`d44b738522ea`) to `642bacb1999e`.

| Class | Endpoint | Finding |
|---|---|---|
| **BROKEN** | `POST /v1/nudges/{}/dismiss` | streak-widget relies on status `200`, which the provider's spec no longer shows. It now lists `204, 401, 404, 409`. |
| CHANGED (relied) | `POST /v1/nudges/{}/dismiss` | Status `204` is newly observed. This is the likely replacement for `200`. |

The change is already recorded as an open linked question (`lq1`, label `contradiction`) in your project's state.

Two gaps are unverified rather than broken, and aren't caused by this change:
- `GET /v1/habits`: the request field `cursor` never appears in the provider's spec.
- `POST /v1/auth/login`: the response field `access_token` never appears in the provider's spec.

## What to do

1. **Adapt the dismiss call.** Treat `204` (no body) as success on `POST /v1/nudges/{}/dismiss`. Stop expecting `200` or a response body, and handle `409` and `404` if you don't already.
2. **Re-pin and re-check.** Run `lattice contracts pin streak-widget`, then `lattice contracts check streak-widget`, to confirm the link is clean.
3. **Clear the question.** Run `lattice contracts questions resolve streak-widget lq1`, or `dismiss` it if you decide it doesn't apply.
4. **If `204` is a surprise to you, ask the streak owner to revert.** That is the alternative to adapting.
5. **Optionally, look at the two gaps.** Ask the streak owner to include `cursor` and `access_token` in the spec, or confirm they're intentionally undocumented.

Two caveats: the check is static only (no live contract run), and streak-widget's own spec is reported as `missing`, so I couldn't map the broken import to specific code locations in your project.
```
