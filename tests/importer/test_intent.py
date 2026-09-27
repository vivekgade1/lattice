"""S3 intent notes (importer I5, spec §3.6): per-feature notes written by the LLM, cached
by commit + per-file hashes, the "code wins" rule in every prompt, and the notes-vs-
behaviour disagreements the report lists."""

from __future__ import annotations

import textwrap
from pathlib import Path

from lattice.importer.intent import (
    CODE_WINS,
    Claim,
    IntentNote,
    IntentNotes,
    disagreements,
    parse_claims,
)
from lattice.llm.client import Generation, Usage

NOTES = """# Notes: things

Users read their own things; admins read every thing.

```json
{"routes": {"read_thing": {"auth": "Customer", "statuses": [200, 404]}}}
```
"""


class CountingGenerator:
    def __init__(self, text: str = NOTES) -> None:
        self.text = text
        self.calls: list[tuple[str, list[dict]]] = []

    def generate(self, system, messages):
        self.calls.append((system, list(messages)))
        return Generation(self.text, Usage(1000, 200), 0.0, "end_turn", model="claude-opus-5")


def _package(root: Path, body: str = "return {'id': thing_id}") -> None:
    pkg = root / "pkg"
    (pkg / "routes").mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "routes" / "__init__.py").write_text("")
    (pkg / "helpers.py").write_text("def norm(x: str) -> str:\n    return x.strip()\n")
    (pkg / "routes" / "things.py").write_text(
        textwrap.dedent(
            f"""\
            from fastapi import APIRouter

            from pkg.helpers import norm

            router = APIRouter()


            @router.get("/things/{{thing_id}}")
            def read_thing(thing_id: str):
                norm(thing_id)
                {body}
            """
        )
    )
    (pkg / "unrelated.py").write_text("X = 1\n")


def _notes(root: Path, cache: Path, gen: CountingGenerator, commit: str | None = "abc"):
    return IntentNotes(root, "pkg", gen, cache_dir=cache, model="m", commit=commit)


def test_notes_are_generated_once_and_cached_by_commit_and_file_hashes(tmp_path):
    root, cache = tmp_path / "repo", tmp_path / "cache"
    _package(root)
    gen = CountingGenerator()
    first = _notes(root, cache, gen).note("pkg.routes.things")
    assert isinstance(first, IntentNote) and not first.cached
    assert len(gen.calls) == 1
    assert "Users read their own things" in first.text
    assert first.claims["read_thing"] == Claim("read_thing", "Customer", (200, 404))
    # the prompt carries the feature's source (its routes' slices) and the rule
    system, messages = gen.calls[0]
    assert CODE_WINS in system
    assert "def read_thing" in messages[0]["content"] and "def norm" in messages[0]["content"]
    assert "X = 1" not in messages[0]["content"]  # a file outside the slices is not shown

    # a fresh instance on the same commit and files reads the cache: no call
    again = _notes(root, cache, gen).note("pkg.routes.things")
    assert again.cached and again.text == first.text and len(gen.calls) == 1
    assert again.key == first.key

    # a file outside the feature changes: still cached
    (root / "pkg" / "unrelated.py").write_text("X = 2\n")
    assert _notes(root, cache, gen).note("pkg.routes.things").cached

    # a file of the feature changes: regenerated
    (root / "pkg" / "helpers.py").write_text("def norm(x: str) -> str:\n    return x\n")
    changed = _notes(root, cache, gen).note("pkg.routes.things")
    assert not changed.cached and changed.key != first.key and len(gen.calls) == 2

    # another commit: regenerated
    other = _notes(root, cache, gen, commit="def").note("pkg.routes.things")
    assert not other.cached and len(gen.calls) == 3


def test_features_group_routes_by_router_module(tmp_path):
    root = tmp_path / "repo"
    _package(root)
    notes = _notes(root, tmp_path / "cache", CountingGenerator())
    assert notes.features() == {"pkg.routes.things": ["read_thing"]}
    assert notes.feature_of("pkg.routes.things", "read_thing") == "pkg.routes.things"


def test_generation_is_charged(tmp_path):
    root = tmp_path / "repo"
    _package(root)
    charged = []
    notes = IntentNotes(root, "pkg", CountingGenerator(), cache_dir=tmp_path / "c",
                        commit="abc", charge=charged.append)  # fmt: skip
    notes.note("pkg.routes.things")
    assert len(charged) == 1 and charged[0].usage.input_tokens == 1000


def test_parse_claims_is_lenient():
    assert parse_claims("no json here") == {}
    assert parse_claims("```json\nnot json\n```") == {}
    got = parse_claims('```json\n{"routes": {"a": {"statuses": ["201", 404]}}}\n```')
    assert got == {"a": Claim("a", None, (201, 404))}


def test_disagreements_compare_claims_with_code_and_verified_behaviour():
    note = IntentNote(
        feature="f",
        text="",
        claims={
            "read_item": Claim("read_item", "Public", (200, 404)),
            "delete_item": Claim("delete_item", "Customer", (200, 403, 404)),
        },
        key="k",
    )
    found = disagreements(
        note,
        auth={"read_item": "Customer", "delete_item": "Customer"},
        observed={"read_item": {200, 403, 404, 401}, "delete_item": {200, 403, 404, 401, 422}},
    )
    by = {(d.route, d.field): d for d in found}
    assert set(by) == {("read_item", "auth"), ("read_item", "statuses")}
    assert by[("read_item", "auth")].notes == "Public"
    assert by[("read_item", "auth")].behaviour == "Customer"
    # 401 and 422 are the framework's, not the feature's: never a disagreement
    assert by[("read_item", "statuses")].behaviour == "200, 403, 404"
    assert by[("read_item", "statuses")].notes == "200, 404"
