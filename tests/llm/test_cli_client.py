"""``lattice.llm.cli_client``: the Claude Code CLI transport (option A -- the CLI is
only a transport, prompts identical to the API arm). Uses the ``fake_claude`` fixture
(``tests/conftest.py``) in place of the real ``claude`` binary; the one real-CLI test is
marked ``live`` (see the bottom of this file) and deselected by default.
"""

from __future__ import annotations

import subprocess

import pytest

from lattice.llm.cli_client import (
    ClaudeCliClient,
    CliError,
    CliTransientError,
    CliUsageLimitError,
    render_conversation,
)
from lattice.llm.client import Generation, GenerationRefused, Usage


def _ok(
    text="hello",
    *,
    stop_reason="end_turn",
    cost=0.0123,
    model="claude-haiku-4-5",
    input_tokens=100,
    output_tokens=20,
    cache_read=0,
    cache_creation=0,
    is_error=False,
):
    return {
        "stdout": {
            "result": text,
            "is_error": is_error,
            "stop_reason": stop_reason,
            "total_cost_usd": cost,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                "output_tokens_details": {"thinking_tokens": 0},
            },
            "modelUsage": {model: {}},
        }
    }


def _error(text, *, exit_code=1):
    return {
        "stdout": {"result": text, "is_error": True, "stop_reason": "stop_sequence"},
        "exit_code": exit_code,
    }


# ---- argument construction -------------------------------------------------------------------


class TestArgumentConstruction:
    def test_isolation_and_transport_flags(self, fake_claude):
        fake_claude.queue([_ok()])
        client = ClaudeCliClient(model="claude-haiku-4-5", effort="high", sleep=lambda s: None)

        client.generate("sys prompt", [{"role": "user", "content": "hi"}])

        argv = fake_claude.calls[0]["argv"]
        assert "-p" in argv
        assert argv[argv.index("--output-format") + 1] == "json"
        assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
        assert argv[argv.index("--system-prompt") + 1] == "sys prompt"
        assert argv[argv.index("--tools") + 1] == ""
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert "--no-session-persistence" in argv
        assert "--strict-mcp-config" in argv
        assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
        assert "--disable-slash-commands" in argv
        assert argv[argv.index("--effort") + 1] == "high"

    def test_neutral_cwd_used_for_every_call(self, fake_claude, tmp_path):
        fake_claude.queue([_ok(), _ok()])
        client = ClaudeCliClient(sleep=lambda s: None)
        assert client.cwd.exists()
        # not the test's own working tree / tmp_path -- a fresh, empty directory
        assert list(client.cwd.iterdir()) == []

        client.generate("s", [{"role": "user", "content": "a"}])
        client.generate("s", [{"role": "user", "content": "b"}])
        # still empty: nothing (e.g. a CLAUDE.md) was ever written into it, and it's
        # reused across calls rather than recreated
        assert list(client.cwd.iterdir()) == []


# ---- stdin content ------------------------------------------------------------------------


class TestStdinContent:
    def test_single_message_sent_verbatim(self, fake_claude):
        fake_claude.queue([_ok()])
        client = ClaudeCliClient(sleep=lambda s: None)

        client.generate("sys", [{"role": "user", "content": "just the user text"}])

        assert fake_claude.calls[0]["stdin"] == "just the user text"

    def test_multi_turn_history_is_delimited_and_identical_in_content(self, fake_claude):
        fake_claude.queue([_ok()])
        client = ClaudeCliClient(sleep=lambda s: None)
        messages = [
            {"role": "user", "content": "first request"},
            {"role": "assistant", "content": "my first answer"},
            {"role": "user", "content": "please fix X"},
        ]

        client.generate("sys", messages)

        stdin = fake_claude.calls[0]["stdin"]
        assert "first request" in stdin
        assert "my first answer" in stdin
        assert "please fix X" in stdin
        # order preserved
        assert stdin.index("first request") < stdin.index("my first answer") < stdin.index(
            "please fix X"
        )


class TestRenderConversation:
    def test_single_message_returned_verbatim(self):
        assert render_conversation([{"role": "user", "content": "hi"}]) == "hi"

    def test_empty_history(self):
        assert render_conversation([]) == ""

    def test_multi_turn_marks_roles(self):
        out = render_conversation(
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
            ]
        )
        assert "USER" in out and "ASSISTANT" in out
        assert out.index("u1") < out.index("a1") < out.index("u2")


# ---- parsing, truncation and error mapping -----------------------------------------------


class TestParsing:
    def test_maps_usage_and_cost(self, fake_claude):
        fake_claude.queue(
            [_ok("the answer", cost=0.4567, input_tokens=111, output_tokens=222, cache_read=33,
                 cache_creation=44, model="claude-opus-5")]
        )
        client = ClaudeCliClient(sleep=lambda s: None)

        result = client.generate("s", [{"role": "user", "content": "u"}])

        assert isinstance(result, Generation)
        assert result.text == "the answer"
        assert result.usage == Usage(111, 222, 33, 44)
        assert result.model == "claude-opus-5"
        assert result.cost_usd == pytest.approx(0.4567)
        assert result.cost_basis == "cli_list_estimate"
        assert result.stop_reason == "end_turn"

    def test_api_transport_leaves_cost_fields_none_by_default(self):
        # the shared Generation type: an unset cost is None, not 0 -- callers must be
        # able to tell "no transport-reported cost" apart from "it cost nothing"
        g = Generation(text="x", usage=Usage(1, 1), latency_s=0.0, stop_reason="end_turn")
        assert g.cost_usd is None
        assert g.cost_basis is None


class TestTruncation:
    def test_max_tokens_stop_reason_is_passed_through(self, fake_claude):
        fake_claude.queue([_ok("cut off partway", stop_reason="max_tokens")])
        client = ClaudeCliClient(sleep=lambda s: None)

        result = client.generate("s", [{"role": "user", "content": "u"}])

        assert result.stop_reason == "max_tokens"
        assert result.text == "cut off partway"


class TestRefusal:
    def test_refusal_stop_reason_raises_generation_refused(self, fake_claude):
        fake_claude.queue([_ok("I can't help with that.", stop_reason="refusal")] * 3)
        client = ClaudeCliClient(sleep=lambda s: None, refusal_retries=0)

        with pytest.raises(GenerationRefused):
            client.generate("s", [{"role": "user", "content": "u"}])

    def test_refusal_is_retried_like_the_api_client(self, fake_claude):
        fake_claude.queue(
            [_ok("nope", stop_reason="refusal"), _ok("the real answer", stop_reason="end_turn")]
        )
        client = ClaudeCliClient(sleep=lambda s: None, refusal_retries=2)

        result = client.generate("s", [{"role": "user", "content": "u"}])

        assert result.text == "the real answer"
        assert result.refusals == 1
        assert len(fake_claude.calls) == 2


class TestErrorMapping:
    def test_is_error_generic_message_raises_cli_error(self, fake_claude):
        fake_claude.queue([_error("model not found")])
        client = ClaudeCliClient(sleep=lambda s: None, max_retries=0)

        with pytest.raises(CliError) as info:
            client.generate("s", [{"role": "user", "content": "u"}])
        assert not isinstance(info.value, CliUsageLimitError)

    def test_nonzero_exit_with_unparsable_output_is_transient_then_raises(self, fake_claude):
        fake_claude.queue(
            [{"stdout": "not json at all", "stderr": "boom", "exit_code": 1}] * 5
        )
        client = ClaudeCliClient(sleep=lambda s: None, max_retries=2)

        with pytest.raises(CliTransientError):
            client.generate("s", [{"role": "user", "content": "u"}])
        assert len(fake_claude.calls) == 3  # first attempt + 2 retries

    def test_usage_limit_message_raises_usage_limit_error_without_retrying(self, fake_claude):
        fake_claude.queue([_error("You have reached your plan's usage limit. Try again later.")])
        client = ClaudeCliClient(sleep=lambda s: None, max_retries=3)

        with pytest.raises(CliUsageLimitError):
            client.generate("s", [{"role": "user", "content": "u"}])
        assert len(fake_claude.calls) == 1  # not retried

    def test_rate_limit_message_is_retried_then_succeeds(self, fake_claude):
        fake_claude.queue([_error("rate limit exceeded, please slow down"), _ok("ok now")])
        sleeps = []
        client = ClaudeCliClient(sleep=sleeps.append, max_retries=2)

        result = client.generate("s", [{"role": "user", "content": "u"}])

        assert result.text == "ok now"
        assert len(fake_claude.calls) == 2
        assert sleeps == [1.0]  # backoff_base * 2**0

    def test_rate_limit_exhausts_retries_and_raises_transient(self, fake_claude):
        fake_claude.queue([_error("rate limit exceeded")] * 5)
        client = ClaudeCliClient(sleep=lambda s: None, max_retries=2)

        with pytest.raises(CliTransientError):
            client.generate("s", [{"role": "user", "content": "u"}])
        assert len(fake_claude.calls) == 3

    def test_timeout_is_transient_and_retried(self, fake_claude, monkeypatch):
        calls = {"n": 0}
        real_run = subprocess.run

        def fake_run(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise subprocess.TimeoutExpired(cmd="claude", timeout=kwargs.get("timeout", 1))
            return real_run(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        fake_claude.queue([_ok("recovered")])
        client = ClaudeCliClient(sleep=lambda s: None, max_retries=1, timeout=1)

        result = client.generate("s", [{"role": "user", "content": "u"}])
        assert result.text == "recovered"
        assert calls["n"] == 2


# ---- the environment: never the API account (review I3) --------------------------------------

API_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
PROVIDER_VARS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


class TestEnvironment:
    def test_api_credentials_and_provider_switches_are_removed(self, fake_claude, monkeypatch):
        for name in (*API_VARS, *PROVIDER_VARS):
            monkeypatch.setenv(name, "set-by-test")
        fake_claude.queue([_ok()])
        ClaudeCliClient(sleep=lambda s: None).generate("s", [{"role": "user", "content": "u"}])
        env = fake_claude.calls[0]["env"]
        assert not set(env) & {*API_VARS, *PROVIDER_VARS}, env

    def test_an_explicit_env_is_scrubbed_too(self, fake_claude):
        import os

        fake_claude.queue([_ok()])
        env = {**os.environ, "ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_USE_BEDROCK": "1"}
        client = ClaudeCliClient(env=env, sleep=lambda s: None)
        client.generate("s", [{"role": "user", "content": "u"}])
        seen = fake_claude.calls[0]["env"]
        assert "ANTHROPIC_API_KEY" not in seen and "CLAUDE_CODE_USE_BEDROCK" not in seen

    def test_the_output_cap_is_the_clients_max_tokens(self, fake_claude, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "99999")
        fake_claude.queue([_ok()])
        client = ClaudeCliClient(max_tokens=1234, sleep=lambda s: None)
        client.generate("s", [{"role": "user", "content": "u"}])
        assert fake_claude.calls[0]["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "1234"


# ---- stop reasons and the output cap -----------------------------------------------------------

CAP_ERROR = (
    "API Error: Claude's response exceeded the 50 output token maximum. To configure this "
    "behavior, set the CLAUDE_CODE_MAX_OUTPUT_TOKENS environment variable."
)


class TestStopReason:
    def test_the_clis_output_cap_error_is_a_max_tokens_stop(self, fake_claude):
        """Verified on the real CLI (2.1.283): past the cap it answers ``is_error`` with
        this text and exit 1. It is the API arm's ``max_tokens`` stop, not a retryable
        failure."""
        cut = _ok(CAP_ERROR, stop_reason="stop_sequence", is_error=True, cost=0.003)
        cut["exit_code"] = 1
        fake_claude.queue([cut, _ok("never reached")])
        result = ClaudeCliClient(sleep=lambda s: None).generate(
            "s", [{"role": "user", "content": "u"}]
        )
        assert result.stop_reason == "max_tokens"
        assert result.text == ""
        assert result.cost_usd == pytest.approx(0.003)
        assert result.usage.output_tokens == 20
        assert len(fake_claude.calls) == 1  # not retried

    def test_a_missing_stop_reason_is_unknown_and_logged(self, fake_claude, caplog):
        response = _ok("hi")
        del response["stdout"]["stop_reason"]
        fake_claude.queue([response])
        with caplog.at_level("WARNING", logger="lattice.llm.cli_client"):
            result = ClaudeCliClient(sleep=lambda s: None).generate(
                "s", [{"role": "user", "content": "u"}]
            )
        assert result.stop_reason == "unknown"
        assert "stop_reason" in caplog.text


# ---- usage limits vs transient rate limits (review I2) -----------------------------------------


PLAN_LIMITS = [
    "You've hit your limit · resets 3pm (America/Chicago)",
    "Claude AI usage limit reached|1759999999",
    "5-hour limit reached ∙ resets 2am",
    "Credit balance is too low",
    "Your weekly limit resets Monday",
]
TRANSIENT = [
    "API Error: 429 Too Many Requests",
    "API Error: 529 Overloaded",
    "Rate limit reached for requests per minute, please retry",
]


class TestLimits:
    @pytest.mark.parametrize("text", PLAN_LIMITS)
    def test_a_plan_limit_in_the_result_stops(self, fake_claude, text):
        fake_claude.queue([_error(text)])
        with pytest.raises(CliUsageLimitError):
            ClaudeCliClient(sleep=lambda s: None).generate("s", [{"role": "user", "content": "u"}])
        assert len(fake_claude.calls) == 1

    @pytest.mark.parametrize("text", PLAN_LIMITS)
    def test_a_plan_limit_on_stderr_stops(self, fake_claude, text):
        fake_claude.queue([{"stdout": "", "stderr": text, "exit_code": 1}])
        with pytest.raises(CliUsageLimitError):
            ClaudeCliClient(sleep=lambda s: None).generate("s", [{"role": "user", "content": "u"}])
        assert len(fake_claude.calls) == 1

    def test_a_plan_limit_on_stderr_beside_json_stops(self, fake_claude):
        response = _error("request failed")
        response["stderr"] = "You've hit your limit · resets 3pm"
        fake_claude.queue([response])
        with pytest.raises(CliUsageLimitError):
            ClaudeCliClient(sleep=lambda s: None).generate("s", [{"role": "user", "content": "u"}])

    @pytest.mark.parametrize("text", TRANSIENT)
    def test_a_transient_rate_limit_is_retried(self, fake_claude, text):
        fake_claude.queue([_error(text), _ok("fine")])
        result = ClaudeCliClient(sleep=lambda s: None).generate(
            "s", [{"role": "user", "content": "u"}]
        )
        assert result.text == "fine" and len(fake_claude.calls) == 2

    @pytest.mark.parametrize("text", TRANSIENT)
    def test_a_transient_rate_limit_on_stderr_is_retried(self, fake_claude, text):
        fake_claude.queue([{"stdout": "", "stderr": text, "exit_code": 1}, _ok("fine")])
        result = ClaudeCliClient(sleep=lambda s: None).generate(
            "s", [{"role": "user", "content": "u"}]
        )
        assert result.text == "fine"


# ---- cost over refused attempts ----------------------------------------------------------------


class TestRefusalCost:
    def test_the_cost_of_refused_attempts_is_added(self, fake_claude):
        fake_claude.queue([_ok("no", stop_reason="refusal", cost=0.01), _ok("yes", cost=0.02)])
        result = ClaudeCliClient(sleep=lambda s: None).generate(
            "s", [{"role": "user", "content": "u"}]
        )
        assert result.refusals == 1
        assert result.cost_usd == pytest.approx(0.03)

    def test_a_final_refusal_carries_every_attempts_cost(self, fake_claude):
        fake_claude.queue([_ok("no", stop_reason="refusal", cost=0.01)] * 3)
        with pytest.raises(GenerationRefused) as info:
            ClaudeCliClient(sleep=lambda s: None).generate("s", [{"role": "user", "content": "u"}])
        assert info.value.cost_usd == pytest.approx(0.03)
        assert info.value.cost_basis == "cli_list_estimate"


# ---- live smoke (deselected by default: `uv run pytest -m live`) ------------------------------


@pytest.mark.live
class TestLiveSmoke:
    def test_real_cli_haiku_tiny_prompt(self):
        import shutil

        if shutil.which("claude") is None:
            pytest.skip("no `claude` binary on PATH")
        client = ClaudeCliClient(model="claude-haiku-4-5")

        result = client.generate(
            "You are terse.", [{"role": "user", "content": "Say the word PONG and nothing else."}]
        )

        assert isinstance(result, Generation)
        assert result.text.strip()
        assert result.usage.input_tokens > 0
        assert result.usage.output_tokens > 0
        assert result.cost_usd is not None and result.cost_usd >= 0
        assert result.cost_basis == "cli_list_estimate"
        print(
            "\nlive CLI smoke:",
            {
                "text": result.text,
                "stop_reason": result.stop_reason,
                "usage": result.usage,
                "cost_usd": result.cost_usd,
                "cost_basis": result.cost_basis,
                "model": result.model,
            },
        )


@pytest.mark.live
class TestLiveCanaries:
    """The round-5 review's real-CLI canaries (``uv run pytest -m live``)."""

    @pytest.fixture(autouse=True)
    def _needs_cli(self):
        import shutil

        if shutil.which("claude") is None:
            pytest.skip("no `claude` binary on PATH")

    def test_it_authenticates_without_the_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-invalid-canary")
        result = ClaudeCliClient(model="claude-haiku-4-5").generate(
            "You are terse.", [{"role": "user", "content": "Say PONG and nothing else."}]
        )
        assert "PONG" in result.text
        assert result.stop_reason == "end_turn"  # reported by the CLI, not defaulted

    def test_the_output_cap_is_enforced(self):
        result = ClaudeCliClient(model="claude-haiku-4-5", max_tokens=50).generate(
            "You are a writer.",
            [{"role": "user", "content": "Write a 400-word essay about rivers."}],
        )
        assert result.stop_reason == "max_tokens"

    def test_the_users_claude_md_is_not_in_context(self):
        from pathlib import Path

        claude_md = Path.home() / ".claude" / "CLAUDE.md"
        if not claude_md.is_file() or "CodeGraph" not in claude_md.read_text():
            pytest.skip("no ~/.claude/CLAUDE.md naming CodeGraph")
        result = ClaudeCliClient(model="claude-haiku-4-5").generate(
            "You are terse.",
            [{"role": "user", "content": 'Does anything in your context besides this message '
              'mention "CodeGraph"? Answer YES or NO only.'}],
        )  # fmt: skip
        assert result.text.strip().upper().startswith("NO")
