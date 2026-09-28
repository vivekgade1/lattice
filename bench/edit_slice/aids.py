"""The context aids of arms L+ and Q+ (round-5 spec 2026-09-28, §7 amendment).

Both arms add the same two aids to their base arm (L+ to L, Q+ to Q), in their own
language:

(a) **Two retrieved worked examples** (:func:`retrieve`, :func:`worked_examples`): the
    most similar *other* tickets by TF-IDF cosine over the ticket text (title and body),
    from the corpus :data:`CORPUS` -- t1-t12, the only tickets with a verified Lattice
    reference -- never the current ticket. t13-t30 are not in the corpus at all, so
    nothing held out can leak. L+ is shown each example's verified Lattice answer, Q+
    its verified Python answer (both exactly the answers the dry run feeds through the
    arms: :func:`bench.edit_slice.run.reference_answer`). The retrieval is the same for
    both arms: same corpus, same exclusion, same count.

(b) **A "common mistakes -> exact fix" table** (:func:`mistakes_section`), read from the
    committed data files :data:`TABLE_FILES`, which ``python -m
    bench.edit_slice.build_aids`` generates deterministically from the round-4 artefacts:
    L+'s from the C3 repair-persistence analysis of arm L's checker refusals, Q+'s from
    round-4 arm-P silent failures as classified by pycheck. Both have the same number of
    rows and the same per-cell cap.

The table goes into the system prompt (it is the same for every ticket), the examples
into the first user message, before the ticket. Nothing else of the base arm's prompt
changes (tests/bench/test_edit_slice_aids.py).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from bench.edit_slice.tickets import ticket

Lang = Literal["lattice", "python"]

# the retrieval corpus: the tickets with a verified Lattice reference (t1-t12)
CORPUS: tuple[str, ...] = (
    "t1_search", "t2_since", "t3_soft_delete", "t4_restore", "t5_title_window", "t6_stats",
    "t7_deactivate", "t8_user_filter", "t9_item_quota", "t10_owner_counts",
    "t11_title_trim", "t12_transfer",
)  # fmt: skip
EXAMPLES = 2  # worked examples per prompt

AIDS_DIR = Path(__file__).resolve().parent / "aids"
TABLE_FILES: dict[Lang, Path] = {
    "lattice": AIDS_DIR / "mistakes_lattice.json",
    "python": AIDS_DIR / "mistakes_python.json",
}

# ---- (a) retrieval --------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def ticket_document(ticket_id: str) -> str:
    """The text a ticket is retrieved by: its title and body (what the model is shown)."""
    t = ticket(ticket_id)
    return f"{t.title}\n{t.text}"


@cache
def _index() -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """(idf over :data:`CORPUS`, per corpus ticket its L2-normalised TF-IDF vector)."""
    docs = {tid: Counter(_tokens(ticket_document(tid))) for tid in CORPUS}
    n = len(docs)
    df = Counter(term for counts in docs.values() for term in counts)
    idf = {term: math.log((1 + n) / (1 + d)) + 1 for term, d in df.items()}
    return idf, {tid: _vector(counts, idf) for tid, counts in docs.items()}


def _vector(counts: Mapping[str, int], idf: Mapping[str, float]) -> dict[str, float]:
    # a term outside the corpus vocabulary has no weight (it matches no document)
    v = {t: c * idf[t] for t, c in counts.items() if t in idf}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


def similarities(ticket_id: str) -> list[tuple[str, float]]:
    """Every corpus ticket except ``ticket_id`` itself, most similar first (cosine of
    TF-IDF vectors; ties by corpus order)."""
    idf, vectors = _index()
    q = _vector(Counter(_tokens(ticket_document(ticket_id))), idf)
    scored = [
        (tid, sum(w * vectors[tid].get(t, 0.0) for t, w in q.items()))
        for tid in CORPUS
        if tid != ticket_id
    ]
    order = {tid: i for i, tid in enumerate(CORPUS)}
    return sorted(scored, key=lambda s: (-s[1], order[s[0]]))


def retrieve(ticket_id: str, k: int = EXAMPLES) -> list[str]:
    """The ``k`` corpus tickets most similar to ``ticket_id`` (never ``ticket_id``)."""
    return [tid for tid, _ in similarities(ticket_id)[:k]]


EXAMPLES_INTRO = (
    "## Worked examples\n\n"
    "Verified solutions to the {n} most similar earlier tickets on the FastAPI template "
    "app, for reference only: they are not part of your ticket, and their files are not "
    "the current files."
)


def worked_examples(ticket_id: str, lang: Lang) -> str:
    """Aid (a): the retrieved tickets and their verified answers in ``lang`` (Lattice for
    L+, Python for Q+), as one section of the first user message."""
    from bench.edit_slice.run import reference_answer

    arm = "L" if lang == "lattice" else "P"
    ids = retrieve(ticket_id)
    parts = [EXAMPLES_INTRO.format(n=len(ids))]
    for i, tid in enumerate(ids, start=1):
        t = ticket(tid)
        answer = reference_answer(arm, tid)
        if answer is None:  # pragma: no cover -- every corpus ticket has both references
            raise AssertionError(f"{tid} has no {lang} reference")
        parts.append(
            f"### Example {i}: {t.title}\n\n{t.text.strip()}\n\n"
            f"#### Its verified solution\n\n{answer.strip()}"
        )
    return "\n\n".join(parts)


# ---- (b) the mistakes table ------------------------------------------------------------------


@dataclass(frozen=True)
class MistakeRow:
    code: str
    mistake: str
    fix: str
    count: int  # how often the source data saw it (the ranking evidence)


MISTAKES_INTRO = (
    "## Common mistakes and their exact fixes\n\n"
    "The errors that edits like this one most often made in earlier runs, each with its "
    "exact fix. Avoid them; if one is reported, apply its fix."
)


@cache
def load_table(lang: Lang) -> tuple[MistakeRow, ...]:
    """The committed table for ``lang`` (see ``bench.edit_slice.build_aids``)."""
    data = json.loads(TABLE_FILES[lang].read_text())
    return tuple(MistakeRow(**row) for row in data["rows"])


def render_table(rows: Sequence[MistakeRow]) -> str:
    items = [
        f"{i}. `{r.code}`: {r.mistake}\n   Exact fix: {r.fix}" for i, r in enumerate(rows, start=1)
    ]
    return MISTAKES_INTRO + "\n\n" + "\n".join(items)


def mistakes_section(lang: Lang) -> str:
    """Aid (b), as one section of the system prompt."""
    return render_table(load_table(lang))


__all__ = [
    "CORPUS",
    "EXAMPLES",
    "TABLE_FILES",
    "MistakeRow",
    "load_table",
    "mistakes_section",
    "render_table",
    "retrieve",
    "similarities",
    "ticket_document",
    "worked_examples",
]
