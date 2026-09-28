"""Coverage without truncation (spec 2026-09-27 surface §4.2), with a fake LLM:

* a truncated answer is never retried with the same settings: the next call asks for
  only the endpoint (a fresh conversation, not the cut-off text); a truncated answer to
  that ends the rung;
* R2 runs on its own generator (the strong model at the R1/R2 effort), R3 on the strong
  one;
* the repo budget is shared per route (the rest of it over the routes still to run), not
  spent first-come; routes run cheapest first (by the size of their code slice);
* the CLI's R1/R2 calls default to 32k output tokens at effort medium, R2 on the strong
  model.
"""

from __future__ import annotations

import pytest

from lattice.importer import cli
from lattice.importer.graph import route_slice, slice_source
from lattice.importer.lift import ImportResult
from lattice.importer.llm_lift import COMPACT, LoopConfig
from tests.importer.test_llm_lift import (
    CALL_COST,
    FakeLLM,
    _buggy,
    _run,
    answer,
    wrong,
)
from tests.importer.test_llm_lift import buggy as buggy  # noqa: F401 -- fixture
from tests.importer.test_llm_lift import good as good  # noqa: F401 -- fixture


def _asks(gen: FakeLLM) -> list[str]:
    return [messages[-1]["content"] for _, messages in gen.calls]


# ---- truncation -----------------------------------------------------------------------------


def test_a_truncated_answer_is_retried_once_asking_for_only_the_endpoint(buggy, good, tmp_path):
    cut = (answer(good["read_item"]) + "x" * 50, "max_tokens")
    gen = FakeLLM(cut, answer(good["read_item"]))
    close = _run(buggy, gen, tmp_path)
    o = close.outcome("read_item")
    assert [(a.rung, a.outcome) for a in o.attempts] == [("R1", "truncated"), ("R1", "verified")]
    first, second = gen.calls
    assert COMPACT not in first[1][-1]["content"]
    # a fresh conversation: the cut-off answer is not sent back
    assert len(second[1]) == 1 and second[1][0]["role"] == "user"
    assert COMPACT in second[1][0]["content"]


def test_a_truncated_compact_answer_ends_the_rung(buggy, tmp_path):
    cut = ("```python\n# lattice: v1\n@endpoint(", "max_tokens")
    gen = FakeLLM(cut, cut)  # R1: the first answer, then the compact one, both cut off
    relift = FakeLLM(cut)  # R2: already compact -- cut off again ends R2 at once
    strong = FakeLLM()  # R3 has no plan here (no simple helper)
    close = _run(buggy, gen, tmp_path, strong_generator=strong, relift_generator=relift)
    o = close.outcome("read_item")
    assert [(a.rung, a.outcome) for a in o.attempts if a.outcome != "skipped"] == [
        ("R1", "truncated"),
        ("R1", "truncated"),
        ("R2", "truncated"),
    ]
    assert len(gen.calls) == 2 and len(relift.calls) == 1 and strong.calls == []
    assert all(COMPACT in ask for ask in [*_asks(gen)[1:], *_asks(relift)])
    assert o.status == "island" and o.reason == "cut off"


def test_other_failures_are_still_repaired_in_one_conversation(buggy, good, tmp_path):
    gen = FakeLLM(wrong(buggy), answer(good["read_item"]))
    close = _run(buggy, gen, tmp_path)
    assert close.outcome("read_item").rung == "R1"
    assert len(gen.calls[1][1]) == 3  # user, assistant, repair
    assert all(COMPACT not in ask for ask in _asks(gen))


def test_r2_runs_on_the_relift_generator_and_r3_on_the_strong_one(buggy, good, tmp_path):
    gen = FakeLLM(*[wrong(buggy)] * 3)
    relift = FakeLLM(answer(good["read_item"]))
    strong = FakeLLM()
    close = _run(buggy, gen, tmp_path, strong_generator=strong, relift_generator=relift)
    o = close.outcome("read_item")
    assert (o.status, o.rung) == ("verified", "R2")
    assert len(relift.calls) == 1 and strong.calls == []


# ---- per-route budgets, cheapest first ------------------------------------------------------


@pytest.fixture(scope="module")
def two_broken() -> ImportResult:
    result = _buggy({"read_items", "read_item", "delete_item"})
    assert [r.key for r in result.needs_llm] == ["read_item", "delete_item"]
    return result


def _route_of(ask: str) -> str:
    return "delete_item" if "def delete_item" in ask else "read_item"


def test_every_route_gets_its_share_of_the_repo_budget(two_broken, tmp_path):
    """A cap of just under two calls: first-come, the first route spent both; shared,
    each route gets one."""
    gen = FakeLLM(*[wrong(two_broken)] * 4)
    config = LoopConfig(method="M1", max_repo_cost_usd=2 * CALL_COST - 0.001)
    close = _run(two_broken, gen, tmp_path, config=config)
    assert len(gen.calls) == 2
    assert sorted(_route_of(a) for a in _asks(gen)) == ["delete_item", "read_item"]
    first, second = (close.outcome(k) for k in [_route_of(a) for a in _asks(gen)])
    assert first.status == second.status == "island"
    assert len(first.attempts) == len(second.attempts) == 1
    assert first.reason.startswith("budget: the route's share of the repo budget")
    assert second.reason.startswith("budget: the repo's cost cap")
    assert first.gap == second.gap == "budget"
    # the outcomes stay in route order
    assert [o.key for o in close.outcomes] == ["read_items", "read_item", "delete_item"]


def test_what_a_route_leaves_goes_to_the_routes_after_it(two_broken, good, tmp_path):
    """The first route verifies in one call of its two-call share: the second gets three."""
    sizes = {k: _slice_size(two_broken, k) for k in ("read_item", "delete_item")}
    cheap, dear = sorted(sizes, key=sizes.__getitem__)

    def reply(system, messages):
        key = _route_of(messages[0]["content"])
        return answer(good[key]) if key == cheap else wrong(two_broken, key)

    gen = FakeLLM(*[reply] * 6)
    config = LoopConfig(method="M1", max_repo_cost_usd=4 * CALL_COST - 0.001)
    close = _run(two_broken, gen, tmp_path, config=config)
    assert close.outcome(cheap).status == "verified"
    assert len(close.outcome(dear).attempts) == 3


def _slice_size(result: ImportResult, key: str) -> int:
    from lattice.importer.graph import build_graph

    r = result.result(key)
    graph = build_graph(result.context.root, result.model.app_package)  # type: ignore[union-attr]
    return len(slice_source(route_slice(graph, f"{r.route.module}:{r.route.name}")))


def test_routes_run_cheapest_first(two_broken, tmp_path):
    sizes = {k: _slice_size(two_broken, k) for k in ("read_item", "delete_item")}
    assert sizes["read_item"] != sizes["delete_item"]
    gen = FakeLLM(*[wrong(two_broken)] * 8)
    config = LoopConfig(method="M1", max_route_rounds=1)
    _run(two_broken, gen, tmp_path, config=config)
    ran = list(dict.fromkeys(_route_of(a) for a in _asks(gen)))
    assert ran == sorted(sizes, key=sizes.__getitem__)


# ---- the CLI's settings ---------------------------------------------------------------------


def test_the_cli_defaults_to_32k_tokens_at_effort_medium_for_r1_and_r2(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    args = cli.parser().parse_args(
        [".", "--package", "p", "--app-factory", "m:f", "--model", "claude-sonnet-5",
         "--strong-model", "claude-opus-5"]
    )  # fmt: skip
    assert args.max_tokens == 32000
    gen, strong, _notes, relift = cli._claude(args)
    assert (gen.model, gen.effort, gen.max_tokens) == ("claude-sonnet-5", "medium", 32000)
    assert (relift.model, relift.effort, relift.max_tokens) == ("claude-opus-5", "medium", 32000)
    assert (strong.model, strong.effort) == ("claude-opus-5", "max")
    # a non-streaming call with 32k output tokens needs an explicit timeout
    assert gen.client.timeout != cli.DEFAULT_SDK_TIMEOUT


def test_the_cli_backend_flag_defaults_to_api():
    args = cli.parser().parse_args([".", "--package", "p", "--app-factory", "m:f"])
    assert args.backend == "api"


def test_the_cli_backend_cli_builds_claude_cli_clients_with_the_same_settings_as_claude(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    args = cli.parser().parse_args(
        [".", "--package", "p", "--app-factory", "m:f", "--model", "claude-sonnet-5",
         "--strong-model", "claude-opus-5", "--backend", "cli"]
    )  # fmt: skip
    assert args.max_tokens == 32000
    gen, strong, notes, relift = cli._cli(args)
    from lattice.llm.cli_client import ClaudeCliClient

    assert isinstance(gen, ClaudeCliClient) and isinstance(strong, ClaudeCliClient)
    assert (gen.model, gen.effort, gen.max_tokens) == ("claude-sonnet-5", "medium", 32000)
    assert (relift.model, relift.effort, relift.max_tokens) == ("claude-opus-5", "medium", 32000)
    assert (strong.model, strong.effort) == ("claude-opus-5", "max")
    assert notes is gen
    # a non-streaming call with 32k output tokens needs the same long timeout as _claude
    assert gen.timeout != cli.DEFAULT_SDK_TIMEOUT and gen.timeout == cli.LONG_CALL_TIMEOUT


def test_the_cli_main_uses_cli_backend_when_asked(tmp_path, monkeypatch):
    """``--backend cli`` (no injected ``generators``) builds ``ClaudeCliClient``s instead
    of talking to the Anthropic API: the loop as a whole is wired to the CLI transport by
    the flag alone. Every fake call errors (``close_gap`` ends that route's ladder on any
    generator exception, per-route, not the whole run -- see ``lattice.importer.llm_lift``
    ``_ask``), so the import still completes; what this checks is that the CLI transport
    was the one asked at all."""
    import lattice.llm.cli_client as cli_client
    from lattice.importer.cli import main
    from tests.importer.test_importer_cli import ARGS

    seen = []

    class _FakeCli:
        def __init__(self, model, max_tokens, effort, timeout=600.0):
            seen.append(model)

        def generate(self, system, messages):
            raise cli_client.CliError("no real call: wiring test only")

    monkeypatch.setattr(cli_client, "ClaudeCliClient", _FakeCli)
    out = tmp_path / "li"
    code = main(
        [*ARGS, "--out", str(out), "--method", "M1", "--backend", "cli",
         "--corpus", str(tmp_path / "c.jsonl")],
    )  # fmt: skip
    assert code == 0
    assert seen  # ClaudeCliClient was constructed, not ClaudeGenerator/anthropic.Anthropic
