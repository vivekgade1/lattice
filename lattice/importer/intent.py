"""S3 of the importer's evidence sources (spec 2026-09-27 importer §3.6): **intent notes**.

An LLM writes one note per *feature* -- a router module and everything its routes reach
in the code graph (:func:`lattice.importer.graph.route_slice`): architecture, data
model, auth and business rules, plus a machine-readable ``claims`` block per route (its
auth role and the statuses it answers). Notes are cached under ``cache_dir`` by a key
over the repository commit, the model, the prompt version, the feature and the hash of
every file the feature's slices touch: a change to any of those files (or a new commit)
regenerates the note; a change elsewhere does not.

Notes are claims, not facts. Every prompt that shows them states :data:`CODE_WINS`, and
nothing is accepted on their say-so: verification decides (spec §3.6 guards).
:func:`disagreements` compares a note's claims with the code's auth decision and the
statuses the original answered while it was verified; each disagreement goes to the
report (it points at a bug in the original or in its docs).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.importer.graph import CodeGraph, build_graph, route_slice
from lattice.llm.client import Generation, Generator

__all__ = [
    "CODE_WINS",
    "Claim",
    "IntentNote",
    "IntentNotes",
    "NotesDisagreement",
    "disagreements",
    "parse_claims",
]

PROMPT_VERSION = "intent-notes/1"
CODE_WINS = "The code wins over the notes when they disagree."
FRAMEWORK_STATUSES = frozenset({401, 422})  # answered by the framework, not the feature
_JSON_FENCE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

SYSTEM = f"""You document one feature of an existing web application for an engineer \
who will re-implement its HTTP routes exactly. You are given the feature's source code: \
its router module and every function, dependency and model its routes reach.

Write concise intent notes in Markdown with these sections: Architecture, Data model, \
Auth, Business rules (per route: what it reads, writes, checks, and every error it \
answers with its status and message). Describe what the code does, not what it should \
do; where a comment or docstring disagrees with the code, say so. {CODE_WINS}

End with one ```json block: {{"routes": {{"<route function name>": {{"auth": \
"Public" | "Customer" | "Admin", "statuses": [<every status the route answers>]}}}}}}."""


@dataclass(frozen=True)
class Claim:
    """What a note claims about one route (None/empty: no claim)."""

    route: str
    auth: str | None = None
    statuses: tuple[int, ...] = ()


@dataclass(frozen=True)
class IntentNote:
    feature: str
    text: str
    claims: Mapping[str, Claim]
    key: str
    cached: bool = False
    files: Mapping[str, str] = field(default_factory=dict)  # module -> sha256


@dataclass(frozen=True)
class NotesDisagreement:
    """The note on ``feature`` says ``notes`` about ``field`` of ``route``; the code or
    the verified behaviour says ``behaviour``."""

    feature: str
    route: str
    field: str
    notes: str
    behaviour: str

    def to_dict(self) -> dict[str, str]:
        return {
            "feature": self.feature,
            "route": self.route,
            "field": self.field,
            "notes": self.notes,
            "behaviour": self.behaviour,
        }


def parse_claims(text: str) -> dict[str, Claim]:
    """The ``claims`` of a note's last ```json block (lenient: anything unreadable is no
    claim)."""
    blocks = _JSON_FENCE.findall(text.replace("\r\n", "\n"))
    if not blocks:
        return {}
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return {}
    routes = data.get("routes") if isinstance(data, dict) else None
    if not isinstance(routes, dict):
        return {}
    out: dict[str, Claim] = {}
    for name, c in routes.items():
        if not isinstance(c, dict):
            continue
        auth = c.get("auth") if isinstance(c.get("auth"), str) else None
        statuses: list[int] = []
        for s in c.get("statuses") or ():
            try:
                statuses.append(int(s))
            except (TypeError, ValueError):
                continue
        out[str(name)] = Claim(str(name), auth, tuple(sorted(set(statuses))))
    return out


def _statuses(values: Iterable[int]) -> str:
    return ", ".join(str(s) for s in sorted(set(values) - FRAMEWORK_STATUSES))


def disagreements(
    note: IntentNote,
    *,
    auth: Mapping[str, str],
    observed: Mapping[str, Iterable[int]],
) -> list[NotesDisagreement]:
    """Each claim of ``note`` the code (``auth``: route -> the extracted role) or the
    verified behaviour (``observed``: route -> the statuses the original answered on
    its suite) contradicts. Framework statuses (401, 422) are left out; a route with no
    observation is not compared on statuses."""
    out: list[NotesDisagreement] = []
    for name, claim in note.claims.items():
        if claim.auth is not None and name in auth and claim.auth != auth[name]:
            out.append(NotesDisagreement(note.feature, name, "auth", claim.auth, auth[name]))
        seen = observed.get(name)
        if claim.statuses and seen is not None:
            said, did = _statuses(claim.statuses), _statuses(seen)
            if said != did:
                out.append(NotesDisagreement(note.feature, name, "statuses", said, did))
    return out


def git_commit(root: Path) -> str | None:
    """The repository's HEAD commit (None outside a git work tree)."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


class IntentNotes:
    """Per-feature notes for the package ``package`` under ``repo_root``, generated with
    ``generator`` and cached under ``cache_dir``. ``charge`` is called with every
    generation (the loop's budget). ``commit``: the repository commit (default: read
    with ``git rev-parse HEAD``; ``None`` outside git)."""

    _UNSET: Any = object()

    def __init__(
        self,
        repo_root: Path | str,
        package: str,
        generator: Generator,
        *,
        cache_dir: Path | str,
        model: str = "",
        commit: str | None = _UNSET,
        graph: CodeGraph | None = None,
        charge: Callable[[Generation], None] | None = None,
    ) -> None:
        self.root = Path(repo_root)
        self.package = package
        self.generator = generator
        self.cache_dir = Path(cache_dir)
        self.model = model
        self.commit = git_commit(self.root) if commit is self._UNSET else commit
        self.graph = graph or build_graph(self.root, package)
        self.charge = charge
        self._notes: dict[str, IntentNote] = {}

    # -- features --

    def features(self) -> dict[str, list[str]]:
        """Feature (router module) -> its route function names, in source order."""
        out: dict[str, list[str]] = {}
        for q in self.graph.routes():
            module, name = q.split(":", 1)
            out.setdefault(module, []).append(name)
        return out

    def feature_of(self, module: str, name: str) -> str:
        return module

    def files(self, feature: str) -> dict[str, str]:
        """Module -> source of every package module the feature's routes reach."""
        modules = {feature}
        for name in self.features().get(feature, []):
            s = route_slice(self.graph, f"{feature}:{name}")
            for q in (*s.functions, *s.models, *s.types, *s.variables):
                sym = self.graph.symbols.get(q)
                if sym is not None:
                    modules.add(sym.module)
        return {m: self.graph.sources[m] for m in sorted(modules) if m in self.graph.sources}

    def key(self, feature: str, files: Mapping[str, str]) -> str:
        hashes = {m: hashlib.sha256(src.encode()).hexdigest() for m, src in files.items()}
        blob = json.dumps(
            {"v": PROMPT_VERSION, "commit": self.commit, "model": self.model,
             "feature": feature, "files": hashes},
            sort_keys=True,
        )  # fmt: skip
        return hashlib.sha256(blob.encode()).hexdigest()

    # -- notes --

    def note(self, feature: str) -> IntentNote:
        if feature in self._notes:
            return self._notes[feature]
        files = self.files(feature)
        key = self.key(feature, files)
        hashes = {m: hashlib.sha256(src.encode()).hexdigest() for m, src in files.items()}
        path = self.cache_dir / f"{_safe(feature)}.{key[:24]}.json"
        if path.exists():
            data = json.loads(path.read_text())
            note = IntentNote(feature, data["text"], parse_claims(data["text"]), key, True, hashes)
        else:
            message = {"role": "user", "content": _prompt(feature, files)}
            gen = self.generator.generate(SYSTEM, [message])
            if self.charge is not None:
                self.charge(gen)
            note = IntentNote(feature, gen.text, parse_claims(gen.text), key, False, hashes)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {"key": key, "feature": feature, "commit": self.commit,
                       "model": self.model, "files": hashes, "text": gen.text}  # fmt: skip
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n")
            tmp.replace(path)
        self._notes[feature] = note
        return note

    def made(self) -> dict[str, IntentNote]:
        """The notes this instance generated or read, by feature."""
        return dict(self._notes)

    def for_route(self, module: str, name: str) -> IntentNote:
        return self.note(self.feature_of(module, name))


def _prompt(feature: str, files: Mapping[str, str]) -> str:
    parts = [f"## Feature `{feature}`", "The feature's source files:"]
    for module, src in files.items():
        parts.append(f"### {module}\n```python\n{src.rstrip()}\n```")
    return "\n\n".join(parts)
