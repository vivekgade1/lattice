"""Claude Code CLI transport ("option A"): the same :class:`~lattice.llm.client.Generator`
interface as :class:`~lattice.llm.client.ClaudeGenerator`, routed through the local
``claude -p`` binary instead of the Anthropic API. The CLI is only a transport -- the
system prompt and user message text sent to the model are byte-identical to the API arm's
(:mod:`bench.edit_slice.prompts`, :mod:`lattice.importer.llm_lift` never see which
transport is in use).

**Isolation flags.** Every call passes ``--tools "" --setting-sources "" --strict-mcp-config
--mcp-config '{"mcpServers":{}}' --disable-slash-commands --no-session-persistence``, and
runs with ``cwd`` set to an empty directory created for the client's lifetime. Verified on
this machine (Claude Code 2.1.283): without ``--strict-mcp-config``/``--mcp-config``, the
user's own MCP tool definitions leak into the prompt (~19.7k tokens on this account); a
cwd inside the repo would let a CLAUDE.md be auto-discovered and injected into the system
prompt. ``--disable-slash-commands`` additionally turns off skills, which would otherwise
be free to fire on a matching prompt. None of ``--system-prompt-file`` or ``--max-turns``
exist on this CLI version (checked via ``claude --help``); the system prompt goes in as
literal text via ``--system-prompt``.

**Multi-turn repair conversations.** The harness/importer's ``Generator.generate(system,
messages)`` takes a full message history (the original request, and for a repair round,
the model's earlier answer plus the follow-up feedback) -- but ``claude -p`` is a one-shot
process with no notion of "here are prior turns, answer the next one". Two options exist:

1. Render the whole history into a single user message, clearly delimited by turn.
2. ``--input-format stream-json`` accepts a JSON-lines list of messages, which *can*
   include prior turns.

This module uses (1). Verified empirically: feeding stream-json a
``user``/``assistant``/``user`` sequence does not replay the injected ``assistant`` line as
fixed context -- the CLI generates its own fresh answer for *every* ``user`` line in the
stream (two user turns produced two separate `result` records, two separate API calls, and
the model's own text, not the one the harness had already committed to and scored). That
would silently double the number of model calls per repair round, bill for turns the
harness never asked for, and let the model contradict the specific answer the checker/smoke
run already reacted to. Rendering the history into one message keeps it to exactly one CLI
invocation per round, with the model seeing precisely the text the API arm would have sent,
identically for both arms P and L (:func:`render_conversation`).

**Cost.** ``claude -p --output-format json`` reports ``total_cost_usd`` for the call (a
list-price estimate, same figure the CLI shows a user). :class:`ClaudeCliClient` copies it
onto :attr:`~lattice.llm.client.Generation.cost_usd` with
:attr:`~lattice.llm.client.Generation.cost_basis` set to ``"cli_list_estimate"``, so callers
that can use a transport-reported cost (:func:`bench.edit_slice.run._account`) prefer it
over recomputing from the price table; the API transport leaves both ``None`` and callers
fall back to the price table as before.

**Errors.** A non-zero exit, ``is_error: true``, or output that isn't parsable JSON all
raise a typed :class:`CliError` (or a subclass); the JSON ``result``, stdout and stderr
are all scanned. A message that looks like a transient rate limit (429, 529, "rate
limit") is :class:`CliTransientError` and is retried with backoff, like a timeout. A
message that looks like the CLI's own plan/usage limit being exhausted ("hit your
limit", "usage limit", "limit reached", "resets ...", "credit balance") is
:class:`CliUsageLimitError` and is *not* retried -- callers (the harness's ``main``) must
let it stop the whole run cleanly rather than record it as a failed run, since there is no
point (or budget) to keep going once the plan is out of usage.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from lattice.llm.client import Generation, GenerationRefused, Usage

__all__ = [
    "ClaudeCliClient",
    "CliError",
    "CliTransientError",
    "CliUsageLimitError",
    "cli_env",
    "render_conversation",
]

DEFAULT_TIMEOUT = 600.0  # seconds per `claude -p` call
DEFAULT_MAX_RETRIES = 2  # transient-error retries, on top of the first attempt
DEFAULT_REFUSAL_RETRIES = 2  # symmetric with lattice.llm.client.ClaudeGenerator's default
DEFAULT_BACKOFF_BASE = 1.0  # seconds; attempt k sleeps backoff_base * 2**k

log = logging.getLogger(__name__)

# Heuristic classification of a failed call's text (the JSON `result`, stdout and stderr).
# A plan/usage-limit message (the account is out of usage until a reset, or out of
# credit) is fatal and must stop the harness; a rate limit (429), an overload (529) or
# another transient-sounding message is retried like a timeout. The plan-limit patterns
# are checked first; "limit reached" counts as a plan limit unless it is a rate limit's.
_USAGE_LIMIT_RE = re.compile(
    r"usage limit|usage cap|hit your limit|plan limit|weekly limit|5-hour limit|"
    r"session limit|out of (usage|credits)|credit balance|\bresets\b|"
    r"(?<!rate )(?<!rate-)(?<!rate_)limit reached",
    re.IGNORECASE,
)
_RATE_LIMIT_RE = re.compile(
    r"rate.?limit|overloaded|too many requests|\b429\b|\b529\b|temporarily unavailable",
    re.IGNORECASE,
)
# The CLI's own output cap (CLAUDE_CODE_MAX_OUTPUT_TOKENS): past it, `claude -p` answers
# is_error with this text (after its own continuation attempts) -- the API's max_tokens.
_OUTPUT_CAP_RE = re.compile(r"exceeded the \d+ output token maximum", re.IGNORECASE)

# Never bill the API account or switch provider: `claude -p` must authenticate as the
# logged-in CLI (bench/envfile.py loads ANTHROPIC_API_KEY into os.environ for the API arm).
_SCRUBBED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
_SCRUBBED_PREFIX = "CLAUDE_CODE_USE_"  # CLAUDE_CODE_USE_BEDROCK / _VERTEX / _FOUNDRY ...
MAX_OUTPUT_ENV = "CLAUDE_CODE_MAX_OUTPUT_TOKENS"


def cli_env(base: Mapping[str, str], max_tokens: int | None) -> dict[str, str]:
    """The environment of a ``claude -p`` call: ``base`` without the API credentials and
    provider switches, with the output cap set to ``max_tokens``."""
    env = {
        k: v
        for k, v in base.items()
        if k not in _SCRUBBED_ENV and not k.startswith(_SCRUBBED_PREFIX)
    }
    if max_tokens is not None:
        env[MAX_OUTPUT_ENV] = str(max_tokens)
    return env

_ISOLATION_FLAGS: tuple[str, ...] = (
    "--tools", "", "--setting-sources", "", "--no-session-persistence",
    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    "--disable-slash-commands",
)  # fmt: skip


class CliError(Exception):
    """``claude -p`` failed: non-zero exit, ``is_error``, or unparsable output."""


class CliTransientError(CliError):
    """A retryable CLI failure: a timeout, a rate limit, or another transient-looking
    server error. :meth:`ClaudeCliClient.generate` retries these with backoff."""


class CliUsageLimitError(CliError):
    """The Claude Code plan's usage limit was hit. Not retryable: callers must stop the
    whole run cleanly instead of recording this as one run's failure."""


def render_conversation(messages: Sequence[Mapping[str, str]]) -> str:
    """``messages`` (the harness/importer's history: the first user turn, then for each
    repair round an assistant turn and a follow-up user turn) as the single stdin prompt
    ``claude -p`` gets. A single message is returned verbatim, so the common (no-repair)
    case is byte-identical to the API arm's first user message. Two or more messages are
    rendered as clearly delimited turns, in the same order and with the same text the API
    arm would have sent as ``messages``; see the module docstring for why this, and not
    ``--input-format stream-json``, is what preserves the harness's exact repair
    semantics."""
    if not messages:
        return ""
    if len(messages) == 1:
        return str(messages[0]["content"])
    parts = [
        "The following is the conversation so far, oldest turn first. Respond only to "
        "the final message below, as your next turn."
    ]
    for msg in messages:
        role = str(msg.get("role", "user")).upper()
        parts.append(f"--- {role} ---\n{msg['content']}")
    return "\n\n".join(parts)


class ClaudeCliClient:
    """Generator backed by ``claude -p`` (the local Claude Code CLI) instead of the
    Anthropic API. Same interface as
    :class:`~lattice.llm.client.ClaudeGenerator`: ``generate(system, messages) ->
    Generation``, with the same refusal-retry behaviour (``refusal_retries``, default 2)
    wrapping a single call so a refused attempt is retried up to that many times before
    surfacing as :class:`~lattice.llm.client.GenerationRefused` -- symmetric with the API
    transport.

    ``max_tokens`` is enforced through the environment (``CLAUDE_CODE_MAX_OUTPUT_TOKENS``;
    no CLI flag exists), so both transports have the same cap. Verified on the real CLI
    (2.1.283, cap 50): it honours the cap, but instead of a ``max_tokens`` stop it makes
    up to three continuation attempts (billed) and then answers ``is_error`` with
    "exceeded the 50 output token maximum" and exit 1, the answer text lost. That is
    returned as a ``stop_reason="max_tokens"`` generation with empty text -- the
    harness's truncation, as on the API -- never retried.

    **Environment.** Every call runs with a copy of the environment (``env`` or
    ``os.environ``, read at call time) without ``ANTHROPIC_API_KEY``,
    ``ANTHROPIC_AUTH_TOKEN``, ``ANTHROPIC_BASE_URL`` and any ``CLAUDE_CODE_USE_*``
    provider switch (:func:`cli_env`): the CLI authenticates as the logged-in user and
    never bills the API account. Verified on the real CLI: with the key removed it
    still authenticates.

    **Stop reason.** The CLI's JSON carries ``stop_reason``; a missing one is recorded as
    ``"unknown"`` (and logged), never assumed to be ``end_turn``.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        max_tokens: int = 16000,
        effort: str = "high",
        *,
        binary: str = "claude",
        cwd: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        refusal_retries: int = DEFAULT_REFUSAL_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        sleep: Callable[[float], None] = time.sleep,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.binary = binary
        self.timeout = timeout
        self.max_retries = max_retries
        self.refusal_retries = refusal_retries
        self.backoff_base = backoff_base
        self._sleep = sleep
        self._env = dict(env) if env is not None else None
        # A neutral, empty cwd: no project CLAUDE.md is discovered and injected into the
        # system prompt. Created once and reused for every call this client makes.
        self.cwd = (
            Path(cwd) if cwd is not None else Path(tempfile.mkdtemp(prefix="lattice-cli-cwd-"))
        )
        self.cwd.mkdir(parents=True, exist_ok=True)

    def _argv(self, system: str) -> list[str]:
        argv = [
            self.binary,
            "-p",
            "--output-format", "json",
            "--model", self.model,
            "--system-prompt", system,
            *_ISOLATION_FLAGS,
        ]  # fmt: skip
        if self.effort:
            argv += ["--effort", self.effort]
        return argv

    # ---- the refusal-retry wrapper (symmetric with ClaudeGenerator.generate) ----------------

    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        spent = Usage(0, 0)
        latency = 0.0
        cost: float | None = None  # the refused attempts' reported cost, summed
        for attempt in range(self.refusal_retries + 1):
            try:
                result = self._generate_once(system, messages)
            except GenerationRefused as refused:
                spent, latency = spent + refused.usage, latency + refused.latency_s
                cost = _add(cost, refused.cost_usd)
                if attempt == self.refusal_retries:
                    refused.usage, refused.latency_s = spent, latency
                    refused.cost_usd = cost
                    refused.cost_basis = "cli_list_estimate" if cost is not None else None
                    raise
                continue
            total = _add(cost, result.cost_usd)
            return Generation(
                text=result.text,
                usage=spent + result.usage,
                latency_s=latency + result.latency_s,
                stop_reason=result.stop_reason,
                model=result.model,
                refusals=attempt,
                cost_usd=total,
                cost_basis="cli_list_estimate" if total is not None else None,
            )
        raise AssertionError("unreachable")

    # ---- one attempt, with transient-error retries and backoff -----------------------------

    def _generate_once(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        prompt = render_conversation(messages)
        argv = self._argv(system)
        for attempt in range(self.max_retries + 1):
            result = self._try_once(argv, prompt)
            if not isinstance(result, CliTransientError):
                return result
            if attempt == self.max_retries:
                raise result
            self._sleep(self.backoff_base * (2**attempt))
        raise AssertionError("unreachable")

    def _try_once(self, argv: list[str], prompt: str) -> Generation | CliTransientError:
        """One subprocess call. Returns the :class:`Generation` on success or a
        :class:`CliTransientError` for the caller to retry; raises directly for a
        non-retryable outcome (:class:`GenerationRefused`, :class:`CliUsageLimitError`,
        or a non-transient :class:`CliError`)."""
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                cwd=str(self.cwd),
                timeout=self.timeout,
                env=cli_env(self._env if self._env is not None else os.environ, self.max_tokens),
            )
        except subprocess.TimeoutExpired:
            return CliTransientError(f"claude -p timed out after {self.timeout}s")
        latency_s = time.perf_counter() - start

        data = None
        if proc.stdout and proc.stdout.strip():
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = None
        stderr = (proc.stderr or "").strip()
        if data is None:
            message = (stderr or proc.stdout or "").strip()[:500] or f"exit {proc.returncode}"
            if _USAGE_LIMIT_RE.search(f"{proc.stdout or ''}\n{stderr}"):
                raise CliUsageLimitError(message)
            if proc.returncode == 0:
                raise CliError(f"claude -p produced no parsable JSON output: {message}")
            return CliTransientError(f"claude -p exited {proc.returncode}: {message}")

        is_error = bool(data.get("is_error"))
        text = data.get("result") or ""
        usage = _usage(data)
        if is_error and _OUTPUT_CAP_RE.search(text):
            return self._generation(data, "", "max_tokens", usage, latency_s)
        if is_error or (proc.returncode != 0 and stderr):
            message = "\n".join(m for m in (text, stderr) if m)[:500]
            message = message or f"claude -p exited {proc.returncode}"
            if _USAGE_LIMIT_RE.search(f"{text}\n{stderr}"):
                raise CliUsageLimitError(message)
            if _RATE_LIMIT_RE.search(f"{text}\n{stderr}") or proc.returncode != 0:
                return CliTransientError(message)
            raise CliError(message)

        stop_reason = data.get("stop_reason")
        if not stop_reason:
            log.warning("claude -p reported no stop_reason; recording it as 'unknown'")
            stop_reason = "unknown"
        if stop_reason == "refusal":
            cost = _cost(data)
            raise GenerationRefused(
                f"model refused to generate: {text[:300]}",
                usage=usage,
                latency_s=latency_s,
                model=self._served(data),
                cost_usd=cost,
                cost_basis="cli_list_estimate" if cost is not None else None,
            )
        return self._generation(data, text, stop_reason, usage, latency_s)

    def _served(self, data: Mapping[str, object]) -> str:
        model_usage = data.get("modelUsage") or {}
        return next(iter(model_usage), "") or self.model  # type: ignore[call-overload]

    def _generation(
        self, data: Mapping[str, object], text: str, stop_reason: str, usage: Usage, latency: float
    ) -> Generation:
        cost = _cost(data)
        return Generation(
            text=text,
            usage=usage,
            latency_s=latency,
            stop_reason=stop_reason,
            model=self._served(data),
            cost_usd=cost,
            cost_basis="cli_list_estimate" if cost is not None else None,
        )


def _usage(data: Mapping[str, object]) -> Usage:
    u = data.get("usage") or {}
    assert isinstance(u, Mapping)
    return Usage(
        input_tokens=int(u.get("input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        cache_read_input_tokens=int(u.get("cache_read_input_tokens") or 0),
        cache_creation_input_tokens=int(u.get("cache_creation_input_tokens") or 0),
    )


def _cost(data: Mapping[str, object]) -> float | None:
    cost = data.get("total_cost_usd")
    return float(cost) if cost is not None else None  # type: ignore[arg-type]


def _add(a: float | None, b: float | None) -> float | None:
    return b if a is None else a if b is None else a + b
