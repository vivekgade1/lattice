# Decision memo: Studio's agent calls on a user's Claude login, or the Agent SDK

Date: 2026-10-30. Audience: the owner. Status: decision pending (section 7).

**Not legal advice.** This memo reads Anthropic's published terms as they were summarised in local research
notes dated 2026-10-24. It is an engineering and risk assessment, not an interpretation of a contract. The terms
and the billing rules for headless use changed several times in 2026, so **they must be re-read on the date of the
decision** (primary pages are listed in section 8), and any reading that matters commercially should go to a
lawyer or to Anthropic in writing.

## 1. The question

Lattice Studio runs its agents (rebuild, plan, chat, MCP discovery) by spawning the `claude -p` binary on the
user's machine, authenticated as whoever is logged in. The owner's standing rule is that no experiment or tool
uses an Anthropic API key. Anthropic's terms, as noted below, say developers building products, including on the
Claude Agent SDK, should use API-key auth. Can Lattice keep the current arrangement, or does it have to move to
the SDK with API keys, and what does each path cost? Handoff section 6 lists this as an owner decision
(`docs/HANDOFF-2026-10-24.md:64-65`) and section 4 flags it as platform risk (`docs/HANDOFF-2026-10-24.md:40-50`).

## 2. What the code does today

- **Policy in code.** `ApiKeyTransportDisabled` says "Project policy (2026-09-29): no experiment or tool calls the
  Anthropic API with an API key"; model calls go through the Claude CLI, which "bills the user's plan"
  (`lattice/llm/client.py:82-93`). The importer raises it for the API arm (`lattice/importer/cli.py:246-248`).
- **The child environment.** `cli_env` removes every `ANTHROPIC_*` and `CLAUDE_CODE_USE_*` variable and
  `AWS_BEARER_TOKEN_BEDROCK`, so `claude -p` can only authenticate as the logged-in CLI. It deliberately keeps
  `HOME`, `PATH` (the Keychain login), `CLAUDE_CONFIG_DIR` and `CLAUDE_CODE_OAUTH_TOKEN`
  (`lattice/llm/cli_client.py:107-132`). Every Studio spawn goes through it: rebuild
  (`lattice/studio/rebuild.py:556`, `:3179`), chat sessions (`lattice/studio/session.py:1199`), the planner
  (`lattice/studio/plan.py:1853`), MCP discovery (`lattice/studio/mcp_intake.py:209`).
- **What is spawned.** Four argv builders: `build_rebuild_argv` (`rebuild.py:1062-1100`, `acceptEdits`, in a git
  worktree), `build_argv` for ask/change chat (`session.py:935-987`, `dontAsk`), `build_planner_argv`
  (`plan.py:707-722`, read-only tools), `discovery_argv` (`mcp_intake.py:140-147`). The importer, drafter and
  benchmarks use `ClaudeCliClient` (`cli_client.py:178`).
- **Documented intent.** The round-5 spec records "I3. The CLI never bills the API account"
  (`docs/archive/superpowers/specs/2026-09-28-lattice-round5-decision-experiment.md:124`, section 8.1 starts at
  `:106`); the reference lists "CLI transport (option A) ... run on the user's plan; the API key scrubbed"
  (`docs/lattice-reference.md:238`). The Claude Code integration design verified `apiKeySource: "none"` against the
  real CLI (`docs/design/lattice-claude-code-integration.md:47`) and states "Sessions use the user's own Claude Code
  login" (`:62`).
- **Not enforced today.** None of the four Studio argvs carries `--max-turns` or `--max-budget-usd`
  (`docs/HANDOFF-2026-10-24.md:49`); only `implement --usd-cap` (`lattice/studio/implement.py:784`) and the
  dogfood shims add a spend cap. This matters for every option below, because a user's login has a plan limit and
  Lattice's parallel plan runs can use a lot of it.

## 3. What Anthropic's terms say (as of the 2026-10-24 notes; re-check)

Sources: the local notes `ecosystem_positioning.md` (lines about the Claude ToS and billing timeline) and
`harness_state_of_the_art.md` (the vendor lock-in item). Both live in the owner's untracked `research_notes/`, so
the facts are restated here in my words with the original URLs they cite. I did not re-fetch any page.

- **Ordinary use.** Subscription OAuth is for a subscriber's "ordinary use" of Claude Code and Anthropic's own
  native apps. Developers building products, explicitly including ones built on the Agent SDK, are told to use
  API-key auth (Console or a supported cloud provider). Third parties may not offer Claude.ai login inside their
  application or route requests through Free/Pro/Max credentials on behalf of users. A customer embedding Claude
  Code must keep the binary unmodified and must not remove its authentication methods; an end user may sign in to
  that unmodified binary with their own subscription. Source: https://code.claude.com/docs/en/legal-and-compliance
  and the Agent SDK overview https://code.claude.com/docs/en/agent-sdk/overview (undated).
- **Third-party harnesses closed.** Subscription tokens were blocked outside the official client on 2026-01-09;
  the docs and consumer terms were tightened on 2026-02-19; on 2026-04-04 Pro/Max limits stopped covering
  third-party harnesses. Sources cited in the notes: https://www.implicator.ai/anthropic-cuts-openclaw-from-claude-subscriptions-citing-unsustainable-compute-costs/,
  https://gigazine.net/gsc_news/en/20260220-anthropic-third-party-block,
  https://letsdatascience.com/news/anthropic-revises-terms-to-bar-third-party-harnesses-b7377cc7
  (secondary reporting; the notes mark parts as such).
- **Headless billing churn.** A 2026-05-14 report said Agent SDK, `claude -p` and GitHub Actions use would move to
  a separate API-rate credit pool from 2026-06-15; Anthropic paused that on 2026-06-15; an update dated 2026-10-07
  says Max and Team plans now include monthly API credits covering the SDK, `claude -p`, the API and Managed
  Agents. The notes call the 2026-10-07 wording ambiguous. Sources: https://support.claude.com/en/articles/15036540,
  https://www.theregister.com/ai-ml/2026/05/14/anthropic-tosses-agents-into-the-api-billing-pool/5240748
- **Enforcement** may happen without notice (same legal page).

Reading for Lattice (inference, not a finding): Studio launches the unmodified `claude` binary on the end user's
machine and the end user is logged in; Lattice holds no credential and offers no login. That is close to the
permitted "end user signs in to the unmodified binary" pattern. What is unsettled is whether a product that
orchestrates many unattended `claude -p` runs (parallel plan nodes, rebuild loops) counts as "ordinary use", and
how headless runs are billed against the plan from month to month.

## 4. Options

**A. Keep `claude -p` on the user's own login (today).** No code change. Each user pays through their own plan,
which fits the no-API-key rule and the verified behaviour (section 2).

**B. Agent SDK with API keys.** Replace the four argv builders and the stream-json parsing
(`session.py`, `rebuild.py`, `plan.py`, `mcp_intake.py`) with SDK calls authenticated by an API key. Follows the
vendor's stated path for products. Reverses the owner's no-API-key rule (`client.py:82`, the I3 invariant) and
needs a key store, cost reporting and a billing story for whoever owns the key.

**C. Hybrid.** Interactive Studio (chat, plan review, local rebuild) stays on the user's login. Unattended or CI use
runs on a key the customer supplies (their own environment, their own bill; Lattice never holds or pays for it).
Needs an explicit opt-in channel through `cli_env` (today it drops every `ANTHROPIC_*` name,
`cli_client.py:115-116`) and a split between the experiment rule (never a key) and the product rule (a
customer's key, on their machine).

**D. Second host.** Add a non-Anthropic agent CLI as a second backend so the verdict layer does not depend on one
vendor's terms. The notes report a more permissive stance for OpenAI's Codex but mark that part unverified. A
separate work item (adapter, tool names and hook semantics differ); it does not answer the Claude question.

## 5. Risks per option

| Option | Main risks |
|---|---|
| A | Terms read against "ordinary use" for heavy unattended runs; billing rules for headless use have changed three times in 2026 and can change again; plan limits stop a run mid-way (`session.py:392` classifies it, no cap prevents it); a CI customer without a Claude login cannot run it; enforcement without notice. |
| B | Breaks the owner's rule and the evidence base (every round used the CLI transport, so results would need a transport re-validation); real money per run instead of plan allowance; Lattice or the customer must manage keys; a large rewrite of files that the H2 freeze file lists (it hashes every tracked file under `lattice/`, `docs/results/2026-10-23-dotnet-h2-prereg.md:214-224`), which only blocks re-running the frozen H2 runners; the SDK drives the same Claude Code binary, so behaviour changes still arrive with CLI releases. |
| C | Two billing modes in the ledger and in the docs; the key opt-in is a new way to leak a credential into a child process; two paths to test; interactive users still depend on the "ordinary use" reading. |
| D | New adapter and a second evidence base (nothing measured on another host); hooks and permission flags differ, so the frame (worktree, protected spec, caps) must be re-proved; does not remove the Claude exposure. |

## 6. What would need to be true

- **A is safe** if: Anthropic confirms (or the legal page is clear on re-reading) that a user running Studio
  with their own login on their own machine is ordinary use; headless billing stays included in the plan or is
  billed to that user; Lattice keeps holding no credential and offering no login; and Studio gets its own caps
  (`--max-turns`, `--max-budget-usd`, cache-inclusive cost) so a run cannot silently exhaust a plan.
- **B is needed** if: Anthropic says orchestrated `claude -p` use in a product must use API-key auth; or the
  billing rule moves headless runs to a pool Lattice cannot let users fund themselves; or a customer needs
  Lattice without Claude Code installed.
- **C is enough** if: only unattended/CI use is affected and interactive use stays covered by A's conditions.
- **D is worth doing** if: vendor-terms risk is judged larger than the cost of a second adapter, independent of
  this question.

## 7. Recommendation and the open owner decision

**Recommendation (the author's; it is not a decision).** Stay on A for now. Do the cheap, reversible work that
makes A defensible whichever way the terms go: add the turn and budget caps to the Studio argvs (already ranked in
the handoff, `docs/HANDOFF-2026-10-24.md:53`); document the stance in user-facing docs (the user's own login, the
unmodified binary, Lattice never stores, proxies or pays for a credential and never offers a login); and write the
C opt-in as a design note only, with the triggers below. Do not start B. Because the handoff calls the question
"unsettled", ask Anthropic in writing whether Studio's pattern is ordinary use; that is the only step that
settles it.

Triggers to revisit: the legal page or the billing article changes wording; a customer asks for CI use; a user
reports their plan exhausted by Studio; Anthropic answers the written question.

**Open owner decision.** (1) Choose A, B, C or D, or A now with C prepared. (2) Decide whether to send Anthropic
the written question. (3) If C is chosen, decide whether the "no API key" rule is narrowed to experiments and
Lattice-owned tooling, with a customer's own key allowed in the product. Until this is decided, nothing in the
repo changes.

## 8. Re-check list on the decision date

Read again, on that day: https://code.claude.com/docs/en/legal-and-compliance,
https://code.claude.com/docs/en/agent-sdk/overview, https://support.claude.com/en/articles/15036540. Record the date
and the exact sentences relied on in the decision record.
