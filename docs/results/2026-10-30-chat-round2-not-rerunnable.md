# Chat round 2 cannot be re-run as registered (note, 2026-10-30)

Factual note; nothing was run and no number changed. Each claim below was checked against the repo on
2026-10-30 (commands in the last section).

## 1. What cannot be re-run, and why

Round 2 (`docs/results/2026-10-18-goto-chat-round2-prereg.md`, results in
`docs/results/2026-10-18-goto-chat-round2-scored-results.md`) was a blind round on a 64-question set. It cannot
be repeated as registered, for two independent reasons.

**1. The blind inputs were never committed.** `docs/results/data/2026-10-18-goto-chat-round2/freeze.sha256` holds
only hashes of the question author's files (23 lines):

| Hash entry in `freeze.sha256` | In the repo? |
|---|---|
| `qset.json` (012c1aae...), the 64 questions | no |
| `chat2_score.py` (c51b12ee...), the wrapper scorer | no |
| `chat2_score_selftest.json` (9d2357d6...) | no |
| `FORMATS2.md` (07161d7a...) | no |
| `MANIFEST.sha256` (25e0addc...) | no |
| `changes/c1_gone_status` ... `changes/c6_reword_control`, 3 files each (18 lines) | no, with one coincidence below |

The files lived in a scratch directory of the orchestrating session: the prereg's command block puts them at
`$SCRATCH/questions/` with `SCRATCH=.../ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/chat2`
(`2026-10-18-goto-chat-round2-prereg.md:414,432`). That `chat2` directory no longer exists (the sibling
`scratchpad/questions/`, which held round 1's files, is empty). The prereg itself warned that "scratch can be
cleaned" for round 1 (`:43-45`); round 2's files were not copied into the repo. What the repo
does hold for round 2 is only the outputs: answers by question id, score summaries, the collected maps and
routers (`docs/results/data/2026-10-18-goto-chat-round2/scored/`) and, under `frozen-r1/`, round 1's own
scorer, question set and candidates (`frozen-r1/qset.json` is round 1's, sha256 4eaca957...).

The one coincidence: the `spec.py` of `changes/c6_reword_control` in `freeze.sha256` is byte-identical to round
1's `frozen-r1/candidates/c6_reword/spec.py` (same sha256). The other 17 candidate files, and every other
listed file, match no tracked file.

`dogfood/chat2_agent.py` cannot start without them: it needs a `--questions` directory holding `qset.json`
(`chat2_agent.py:338-339`) and `chat2_score.py` (`:291`).

**2. The frozen build no longer verifies.** `chat2_agent.py` calls `CP.verify_freeze(out.parent)` first
(`chat2_agent.py:333`), which exits 2 on any difference, "there is no switch" (`dogfood/chat2_probes.py:1126-1140`).
The check is against `freeze-build.sha256` (17 files). Post hoc commit 6527477 (2026-10-18, "chat round 2 (post
hoc, not blind): a partial-section change_impact document no longer crashes the claim checker") changed
`lattice/spec/claim_check.py` and two hashed tests. `shasum -a 256 -c docs/results/data/2026-10-18-goto-chat-round2/freeze-build.sha256`
now reports exactly three failures: `lattice/spec/claim_check.py`, `tests/spec/test_claim_check.py`,
`tests/studio/test_chat_check_session.py`. The fix itself repairs the defect behind the round's G3 failure (six
`I_free` sessions crashed with `KeyError: 'tier2'`, scored results section on G3), so running the old build to
reproduce it would also mean reverting the fix.

## 2. What the registered round would have to be, and is not

- The round-2 budget (USD 8.00) is spent (7.528 per the scored results); the results already say a rerun of the
  failing questions "needs a new approved budget".
- The round's answers were read and scored, and the defect that failed G3 was found in this round and fixed
  afterwards. Even with the files recovered, a repeat would run the fixed build on questions that already
  produced a known failure, so it would not be blind.

## 3. What a new round would need

1. **A new question author** (separate from the builder, as round 2 did) writing a new set, a scorer with its
   self-test, a format document and the change candidates, **committed under `docs/results/data/<date>-.../` before
   any run**, with the hashes in a freeze file that points at those committed files.
2. **A new scorer wrapper** that does not look for `frozen-r1/goto_score.py` (one level up) next to a scratch question
   directory (that lookup is why round 2's own scoring call exited 2, scored results deviation D1). Either vendor
   round 1's scorer into the repo with its hash or have the wrapper take an explicit path.
3. **A new freeze of the current build** (`freeze-build.sha256` regenerated from the tree that includes 6527477
   and any later changes), written before the first model call, with `verify_freeze` reading it from the run's
   own directory (round 2 needed a byte copy under `scored/` for this, deviation D2).
4. A new pre-registration and an approved budget, a ledger and the usual caps (see
   `docs/design/harness-facts-for-spike-briefs.md`).
5. **If the owner only wants to know whether the fix works**: a non-blind fix-and-rerun on the six `I_free` and
   six `I_routed` ablation questions, labelled as such, using the unit and session tests added by 6527477
   (12 unit and 7 session tests) first. That is a regression check, not a blind result, and must not be
   reported as one.

## 4. How each claim was checked (2026-10-30, from this worktree)

- Listing the hashes of `freeze.sha256` and computing the sha256 of every tracked file: only the `c6` spec matched
  (to round 1's candidate file).
- `git rev-list --all --objects` for `chat2_score`, `FORMATS2`, `c1_gone_status` and `c2_register_default`: no match
  in any ref.
- `ls` of the recorded scratch `questions/` directory: empty; no `chat2_score.py` or `FORMATS2.md` under the
  session scratch roots or under `~/Documents` to four levels.
- `shasum -a 256 -c .../freeze-build.sha256`: 3 FAILED (listed above), 14 OK.
- `git log -- lattice/spec/claim_check.py`: 107074b (build), 6527477 (post hoc fix).
