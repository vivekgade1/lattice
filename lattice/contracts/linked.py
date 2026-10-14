"""Linked pending questions in consumers (multi-project slice 4b): when a provider's contract
changes in a way that BROKE a consumer's pin, the consumer's own state directory gets an advisory
note, a *linked question*, that only the consumer's owner clears.

    lattice contracts notify PROVIDER --candidate REF|PATH [--dry-run] [--json]
    lattice contracts questions list|resolve|dismiss|init CONSUMER ...

**Advisory.** Nothing reads a question to decide anything: no gate, build, pin, check, plan or
accept looks at ``linked_questions/``. A provider's accept is never blocked, delayed or altered
by a delivery (lattice/studio/server.py runs it on a background worker).

**What is computed.** Slice 4a's :func:`lattice.contracts.impact.run_impact`, unchanged: each
opted-in consumer's pin against the candidate. Only a ``contradiction`` row creates a question
(``regression``, ``unaffected``, gaps and error rows never do).

**Idempotence (the key).** ``key = sha256(canonical JSON [provider, consumer, contract, the
pin's spec_hash, sorted [endpoint, kind, item] of every BROKEN finding])``: no candidate ref or
hash, so a later change that leaves the same reliances broken is the same question. A
notification creates a question unless an ``open`` question with the key exists, or the link's
``current`` already points at a question with the key. A resolved or dismissed question is never
reopened; a break that returns after the link was in another state is a NEW question.

**Supersede (derived, never auto-cleared).** Per (provider, contract) the consumer holds a
``links/<provider>.<contract>.json`` with ``current`` (the question of the latest contradicting
row, else null). An ``open`` question whose link's ``current`` is not its id reads as
``superseded``; no status is ever changed by a notification.

**The cross-project write** is the first one by one project's process into another's state, so
it is minimal: only ``linked_questions/{.lock, lq<N>.json, links/*.json}`` (and their transient
``.<name>.tmp-*``) are ever written in a foreign state directory, atomically
(:func:`lattice.studio.state.atomic_write`) under an exclusive ``fcntl.flock`` on
``linked_questions/.lock``, never through ``StateStore`` (whose ``allocate`` rewrites a
``meta.json`` that a running Studio holds in memory), and a missing state directory is never
created by a provider (``lattice contracts questions init CONSUMER`` creates it, as the
consumer's owner).
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import fcntl
import hashlib
import json
import os
import re
import time
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from lattice.contracts import impact as imp
from lattice.studio.state import _now, _slug, atomic_write, state_dir_for, state_dir_path
from lattice.workspace import ERROR, Workspace

FORMAT = 1
DIR = "linked_questions"
LINKS = "links"
LOCK = ".lock"
LOCK_WAIT = 5.0  # seconds a write waits for the flock before consumer_state_busy
NOTE_MAX = 2000
OPEN, RESOLVED, DISMISSED = "open", "resolved", "dismissed"
STATUSES = (OPEN, RESOLVED, DISMISSED)
_QFILE = re.compile(r"^lq(\d+)\.json(?:\.bad-.*)?$")
_QID = re.compile(r"^lq[1-9]\d*$")

CREATED, EXISTING, NONE, UNDELIVERED, SKIPPED = (
    "created", "existing", "none", "undelivered", "skipped",
)  # fmt: skip
ACTIONS = (CREATED, EXISTING, NONE, UNDELIVERED, SKIPPED)

HTTP = {
    "unknown_question": 404,
    "no_state": 404,
    "not_open": 409,
    "bad_note": 422,
    "bad_status": 422,
    "consumer_state_busy": 503,
}  # anything else (a delivery failure) answers 409


class LinkedError(Exception):
    """A refusal with a ``code`` (``unknown_question``, ``not_open``, ``bad_note``,
    ``no_state``, ``bad_status``, or a ``consumer_state_*`` delivery code) and a message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> int:
        return HTTP.get(self.code, 409)


# ---- the question ----------------------------------------------------------------------------


def finding_line(f: Mapping[str, Any]) -> str:
    """One finding as ``lattice contracts check`` prints it."""
    tag = f["class"] if f["class"] != "CHANGED" else ("CHANGED*" if f.get("relied") else "CHANGED")
    where = f"  {f['where']}" if f.get("where") else ""
    item = f"  {f['item']}" if f["item"] else ""
    return f"  {tag:<8} {f['kind']}{where}{item}  @ {f['endpoint']}"


def key_of(provider: str, consumer: str, contract: str | None, row: Mapping[str, Any]) -> str:
    """The idempotence key of a contradiction row (see the module docstring)."""
    broken = sorted([f["endpoint"], f["kind"], f["item"]]
                    for f in row["findings"] if f["class"] == "BROKEN")  # fmt: skip
    doc = [provider, consumer, contract, row["pinned"]["spec_hash"], broken]
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _short(version: Mapping[str, Any] | None) -> str:
    return ((version or {}).get("git_ref") or "no-git")[:8]


def build_question(
    qid: str,
    key: str,
    ws_name: str | None,
    row: Mapping[str, Any],
    candidate: Mapping[str, Any],
    source: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """The ``lq<N>.json`` object of a contradiction row (every key always present)."""
    provider, consumer = row["provider"], row["consumer"]
    findings = [f for f in row["findings"] if f["class"] == "BROKEN" or f.get("relied")]
    broken = [f for f in row["findings"] if f["class"] == "BROKEN"]
    affected = sorted({f["endpoint"] for f in broken})
    ver = candidate.get("version") or {}
    after = {"spec_ref": candidate.get("spec_ref"), "spec_hash": ver.get("spec_hash"),
             "git_ref": ver.get("git_ref"), "dirty": ver.get("dirty")}  # fmt: skip
    head = (f"{provider}'s accepted contract (spec_hash {str(ver.get('spec_hash'))[:12]}, commit "
            f"{_short(ver)}) contradicts {consumer}'s pin (spec_hash "
            f"{row['pinned']['spec_hash'][:12]}): {len(broken)} import(s) BROKEN on "
            f"{len(affected)} endpoint(s)")  # fmt: skip
    text = "\n".join([head, *(finding_line(f) for f in findings)])
    suggestion = (f"advisory, nothing was changed in {consumer}: adapt {consumer} to the "
                  f'findings, then "lattice contracts pin {consumer}" and "lattice contracts '
                  f'check {consumer}"; or ask {provider}\'s owner to revert. Clear it with '
                  f'"lattice contracts questions resolve|dismiss {consumer} {qid}"')  # fmt: skip
    return {
        "format": FORMAT, "id": qid, "key": key, "kind": "linked_question",
        "workspace": ws_name, "provider": provider, "consumer": consumer,
        "contract": row["contract"], "status": OPEN, "label": "contradiction",
        "pinned": dict(row["pinned"]), "provider_after": after,
        "candidate": {"input": candidate.get("input"), "kind": candidate.get("kind"),
                      "resolved": candidate.get("resolved")},
        "imports_affected": affected, "findings": findings, "counts": dict(row["counts"]),
        "text": text, "suggestion": suggestion, "source": dict(source),
        "created_at": created_at, "resolution": None,
    }  # fmt: skip


# ---- locating a consumer's state directory ---------------------------------------------------


@dataclasses.dataclass
class Location:
    """Where a consumer's state lives: ``state`` is ``present`` (its directory exists),
    ``missing`` or ``disabled`` (``[state] enabled = false``); ``state_dir`` the directory (for
    ``missing`` the one a Studio would create), ``warnings`` ``{code, message}`` rows."""

    state: str
    state_dir: Path | None
    warnings: list[dict[str, str]] = dataclasses.field(default_factory=list)
    names: list[str] = dataclasses.field(default_factory=list)
    tables: tuple[dict[str, Any], dict[str, Any]] = dataclasses.field(
        default_factory=lambda: ({}, {})
    )


def _studio_toml(project: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads((project / "studio.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return data


def _table(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    t = data.get(name)
    return dict(t) if isinstance(t, dict) else {}


def locate(project: Path, manifest_name: str | None = None) -> Location:
    """The consumer's state directory (module docstring, D3.1): its ``studio.toml`` read with
    ``tomllib`` only; ``[state] enabled = false`` is ``disabled``; ``[state] dir`` that
    directory; else ``state_dir_path`` for the project's own name, then the manifest name, the
    first that exists wins (two existing: the first, with ``consumer_state_ambiguous``). Never
    creates or writes anything."""
    from lattice.cli import project_name

    project = Path(project).resolve()
    toml = _studio_toml(project)
    state_t, rebuild_t = _table(toml, "state"), _table(toml, "rebuild")
    if state_t.get("enabled", True) is False:
        return Location("disabled", None, [], [], (state_t, rebuild_t))
    names = list(dict.fromkeys(n for n in (project_name(project, toml), manifest_name) if n))
    if isinstance(state_t.get("dir"), str) and state_t["dir"].strip():
        names = names[:1]  # the name does not matter: one directory
    cands = list(dict.fromkeys(state_dir_path(project, n, state_t, rebuild_t) for n in names))
    have = [c for c in cands if c.is_dir()]
    warns: list[dict[str, str]] = []
    if len(have) > 1:
        warns.append({"code": "consumer_state_ambiguous", "message": "several state directories "
                      f"exist ({', '.join(str(h) for h in have)}); the first is used"})  # fmt: skip
    if have:
        return Location("present", have[0], warns, names, (state_t, rebuild_t))
    return Location("missing", cands[0], warns, names, (state_t, rebuild_t))


def init_state(project: Path, manifest_name: str | None = None) -> tuple[Path, bool]:
    """The consumer's owner creates its state directory (through the real ``state_dir_for``,
    the git exclusion included) and ``linked_questions/``. Idempotent. Returns
    ``(state dir, created)``. Raises :class:`LinkedError` ``consumer_state_disabled``."""
    loc = locate(project, manifest_name)
    if loc.state == "disabled":
        raise LinkedError("consumer_state_disabled",
                          f"{project}: [state] enabled = false in its studio.toml")  # fmt: skip
    created = loc.state == "missing"
    if created:
        state_t, rebuild_t = loc.tables
        d = state_dir_for(project, loc.names[0], state_t, rebuild_t)
        d.mkdir(parents=True, exist_ok=True)
    else:
        assert loc.state_dir is not None
        d = loc.state_dir
    q = d / DIR
    if q.is_symlink() or (q.exists() and not q.is_dir()):
        raise LinkedError("consumer_state_unsafe", f"{q} is a symlink or not a directory")
    q.mkdir(exist_ok=True)
    return d, created


# ---- reading (no lock, writes nothing) -------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _scan(qdir: Path) -> tuple[list[tuple[int, str, dict[str, Any] | None]], int]:
    """``[(N, file name, parsed or None)]`` of the ``lq<N>.json*`` files in id order, and the
    highest N among them (unreadable and ``.bad-*`` names included)."""
    out: list[tuple[int, str, dict[str, Any] | None]] = []
    top = 0
    try:
        names = sorted(os.listdir(qdir))
    except OSError:
        return [], 0
    for name in names:
        m = _QFILE.match(name)
        if not m:
            continue
        n = int(m.group(1))
        top = max(top, n)
        if name.endswith(".json"):
            out.append((n, name, _read_json(qdir / name)))
    out.sort(key=lambda t: t[0])
    return out, top


def _links(qdir: Path) -> dict[str, dict[str, Any]]:
    """``{file name: link}`` of ``links/*.json`` (unreadable ones skipped)."""
    out: dict[str, dict[str, Any]] = {}
    try:
        names = sorted(os.listdir(qdir / LINKS))
    except OSError:
        return out
    for name in names:
        if name.endswith(".json") and not name.startswith("."):
            doc = _read_json(qdir / LINKS / name)
            if doc is not None:
                out[name] = doc
    return out


def link_name(provider: str, contract: str | None) -> str:
    return f"{_slug(provider)}.{contract}.json"


def _superseded(q: Mapping[str, Any], links: Mapping[str, Mapping[str, Any]]) -> dict | None:
    if q.get("status") != OPEN:
        return None
    link = links.get(link_name(str(q.get("provider")), q.get("contract")))
    if link is None or link.get("current") == q.get("id"):
        return None
    cur = link.get("current")
    return {"by": cur, "reason": "newer_question" if cur else "no_contradiction",
            "since": link.get("since")}  # fmt: skip


def read_state(state_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Every question under ``state_dir/linked_questions`` in id order, each with the derived
    ``superseded`` (``None`` or ``{by, reason, since}``), and the problems met
    (``unreadable_question``, ``newer_format``)."""
    qdir = Path(state_dir) / DIR
    rows, _top = _scan(qdir)
    links = _links(qdir)
    qs: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    for _n, name, doc in rows:
        if doc is None or doc.get("kind") != "linked_question":
            problems.append(
                {
                    "code": "unreadable_question",
                    "file": name,
                    "message": f"{name} is not a readable linked question (kept as is)",
                }
            )
            continue
        fmt = doc.get("format")
        if isinstance(fmt, int) and fmt > FORMAT:
            problems.append(
                {
                    "code": "newer_format",
                    "file": name,
                    "message": f"{name} has "
                    f"format {fmt} (this reader knows {FORMAT}); unknown fields kept",
                }
            )
        qs.append({**doc, "superseded": _superseded(doc, links)})
    return qs, problems


def counts_of(questions: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        OPEN: sum(1 for q in questions if q.get("status") == OPEN),
        RESOLVED: sum(1 for q in questions if q.get("status") == RESOLVED),
        DISMISSED: sum(1 for q in questions if q.get("status") == DISMISSED),
        "superseded": sum(1 for q in questions if q.get("superseded")),
    }


def list_body(
    consumer: str, state: str, state_dir: Path | None, status: str | None = None,
    warnings: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:  # fmt: skip
    """The list answer (CLI ``--json``, MCP, ``GET /api/linked-questions``): ``format``,
    ``consumer``, ``state`` (present|missing|disabled), ``state_dir``, ``questions`` (id order,
    filtered by `status` when given and not ``all``), ``counts`` (over every question) and
    ``problems``. Raises :class:`LinkedError` ``bad_status`` for an unknown status."""
    if status not in (None, "all", *STATUSES):
        raise LinkedError("bad_status", f"status must be one of {', '.join((*STATUSES, 'all'))}")
    qs: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = [dict(w) for w in warnings]
    if state == "present" and state_dir is not None:
        qs, more = read_state(state_dir)
        problems += more
    shown = [q for q in qs if status in (None, "all") or q.get("status") == status]
    return {"format": FORMAT, "consumer": consumer, "state": state,
            "state_dir": str(state_dir) if state_dir is not None else None,
            "questions": shown, "counts": counts_of(qs), "problems": problems}  # fmt: skip


def get_question(state_dir: Path, qid: str) -> dict[str, Any]:
    """One question (with ``superseded``), or :class:`LinkedError` ``unknown_question``."""
    if _QID.match(qid or ""):
        qs, _problems = read_state(state_dir)
        for q in qs:
            if q.get("id") == qid:
                return q
    raise LinkedError("unknown_question", f"no linked question {qid!r}")


# ---- writing (under the flock) ---------------------------------------------------------------


def _safe_dir(path: Path, create: bool) -> bool:
    """True when `path` is a real directory (made, one level, when `create`); a symlink or a
    non-directory raises ``consumer_state_unsafe``."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise LinkedError("consumer_state_unsafe", f"{path} is a symlink or not a directory")
    if not path.exists():
        if not create:
            return False
        path.mkdir(exist_ok=True)  # one level: the state directory must already exist
    return True


@contextlib.contextmanager
def locked(state_dir: Path, *, wait: float = LOCK_WAIT, create: bool = True) -> Iterator[Path]:
    """The consumer's ``linked_questions`` directory under an exclusive flock on ``.lock`` (one
    open per call, so two Studios of one process also exclude each other); stale temporaries
    swept. Raises :class:`LinkedError`: ``consumer_state_unsafe`` (a symlink or a non-directory),
    ``consumer_state_unwritable`` (any OSError, read-only included), ``consumer_state_busy``
    (the lock is held for more than `wait` seconds)."""
    qdir = Path(state_dir) / DIR
    fd = -1
    try:
        try:
            _safe_dir(qdir, create)
        except FileNotFoundError:  # no state directory: a provider never creates one
            raise LinkedError("consumer_state_missing",
                              f"{state_dir} does not exist") from None  # fmt: skip
        if not os.access(qdir, os.W_OK | os.X_OK):
            raise LinkedError("consumer_state_unwritable", f"{qdir} is not writable")
        fd = os.open(qdir / LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise LinkedError("consumer_state_busy", f"{qdir / LOCK} is held by "
                                      "another process") from None  # fmt: skip
                time.sleep(0.02)
        _safe_dir(qdir / LINKS, True)
        for d in (qdir, qdir / LINKS):  # temporaries of a write a crash interrupted
            for p in d.glob(".*.tmp-*"):
                with contextlib.suppress(OSError):
                    p.unlink()
        yield qdir
    except LinkedError:
        raise
    except OSError as e:
        raise LinkedError("consumer_state_unwritable", f"{qdir}: {e}") from None
    finally:
        if fd >= 0:
            os.close(fd)  # releases the flock


def _put(path: Path, doc: Mapping[str, Any]) -> None:
    atomic_write(path, json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8"))


def _link_doc(row: Mapping[str, Any], current: str | None, candidate: Mapping[str, Any],
              source: Mapping[str, Any]) -> dict[str, Any]:  # fmt: skip
    ver = candidate.get("version") or {}
    return {"format": FORMAT, "provider": row["provider"], "consumer": row["consumer"],
            "contract": row["contract"], "current": current, "since": _now(),
            "provider_after": {"spec_ref": candidate.get("spec_ref"),
                               "spec_hash": ver.get("spec_hash"), "git_ref": ver.get("git_ref"),
                               "dirty": ver.get("dirty")},
            "source": dict(source)}  # fmt: skip


def deliver(
    state_dir: Path,
    ws_name: str | None,
    row: Mapping[str, Any],
    candidate: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    dry_run: bool = False,
    wait: float = LOCK_WAIT,
) -> dict[str, Any]:
    """Apply one evaluated impact row to the consumer's state directory (module docstring):
    returns ``{action, question, current}``. The decision is made on a lock-free read first and
    a row that needs no write (a clean one, a repeated one) writes nothing at all, not even the
    lock file; otherwise it is made again under the flock and written. ``dry_run`` decides and
    writes nothing. Raises :class:`LinkedError` for the ``consumer_state_*`` failures."""
    qdir = Path(state_dir) / DIR
    result, puts = _plan(qdir, ws_name, row, candidate, source)
    if not puts:
        return result
    if dry_run:
        if not Path(state_dir).is_dir():
            raise LinkedError("consumer_state_missing", f"{state_dir} does not exist")
        if qdir.is_symlink() or (qdir.exists() and not qdir.is_dir()):
            raise LinkedError("consumer_state_unsafe", f"{qdir} is a symlink or not a directory")
        return result
    with locked(state_dir, wait=wait) as qdir:
        try:
            result, puts = _plan(qdir, ws_name, row, candidate, source)
            for path, doc in puts:
                _put(path, doc)
        except OSError as e:
            raise LinkedError("consumer_state_unwritable", f"{qdir}: {e}") from None
    return result


def _plan(
    qdir: Path,
    ws_name: str | None,
    row: Mapping[str, Any],
    candidate: Mapping[str, Any],
    source: Mapping[str, Any],
) -> tuple[dict[str, Any], list[tuple[Path, dict[str, Any]]]]:
    """The decision for one row against the files as they are: the result and the files to put
    (a question and/or the link). Reads only."""
    rows, top = _scan(qdir)
    by_id = {f"lq{n}": doc for n, _name, doc in rows if doc is not None}
    lname = link_name(row["provider"], row["contract"])
    link = _read_json(qdir / LINKS / lname)
    cur = link.get("current") if link else None
    puts: list[tuple[Path, dict[str, Any]]] = []
    if row["label"] != imp.CONTRADICTION:
        if link is not None and cur is not None:  # the break is gone: current -> null
            puts.append((qdir / LINKS / lname, _link_doc(row, None, candidate, source)))
        return {"action": NONE, "question": None, "current": None}, puts
    key = key_of(row["provider"], row["consumer"], row["contract"], row)
    qid: str | None = None
    for n, _name, doc in rows:  # an open question with the key
        if doc is not None and doc.get("key") == key and doc.get("status") == OPEN:
            qid = f"lq{n}"
            break
    if qid is None and cur in by_id and by_id[cur].get("key") == key:  # the state is unchanged
        qid = cur
    action = EXISTING
    if qid is None:
        qid, action = f"lq{top + 1}", CREATED
        q = build_question(qid, key, ws_name, row, candidate, source, _now())
        puts.append((qdir / f"{qid}.json", q))
    if cur != qid:
        puts.append((qdir / LINKS / lname, _link_doc(row, qid, candidate, source)))
    return {"action": action, "question": qid, "current": qid}, puts


def clear(state_dir: Path, qid: str, status: str, note: Any, via: str,
          *, wait: float = LOCK_WAIT) -> dict[str, Any]:  # fmt: skip
    """Resolve or dismiss an ``open`` question (the consumer's owner): returns the updated
    question. Raises :class:`LinkedError`: ``bad_note`` (not a string, or over 2000
    characters), ``unknown_question``, ``not_open`` (the stored resolution is unchanged)."""
    assert status in (RESOLVED, DISMISSED)
    text = "" if note is None else note
    if not isinstance(text, str) or len(text) > NOTE_MAX:
        raise LinkedError("bad_note", f"note must be a string of at most {NOTE_MAX} characters")
    if not _QID.match(qid or "") or not (Path(state_dir) / DIR).is_dir():
        raise LinkedError("unknown_question", f"no linked question {qid!r}")
    with locked(state_dir, wait=wait, create=False) as qdir:
        path = qdir / f"{qid}.json"
        doc = _read_json(path)
        if doc is None or doc.get("kind") != "linked_question":
            raise LinkedError("unknown_question", f"no linked question {qid!r}")
        if doc.get("status") != OPEN:
            raise LinkedError("not_open", f"{qid} is already {doc.get('status')}")
        doc["status"] = status
        doc["resolution"] = {"status": status, "note": text, "at": _now(), "via": via}
        _put(path, doc)
    return get_question(state_dir, qid)


# ---- the notification ------------------------------------------------------------------------


def _warning(e: LinkedError) -> dict[str, str]:
    return {"code": e.code, "message": e.message}


def notify(
    ws: Workspace,
    provider: str,
    candidate: str,
    *,
    via: str,
    base: Path | None = None,
    change: str | None = None,
    commit: str | None = None,
    dry_run: bool = False,
    lock_wait: float = LOCK_WAIT,
) -> dict[str, Any]:
    """Compute slice 4a's impact of `candidate` on `provider` and deliver one question per
    contradicting consumer row (module docstring). Never raises on content; the result is the
    ``lattice contracts notify --json`` document."""
    t0 = time.monotonic()
    rep = imp.run_impact(ws, provider, candidate, base=base)
    cand = rep.candidate
    ver = cand.get("version") or {}
    sha = (
        commit
        if commit is not None
        else (cand.get("resolved") if cand.get("kind") == "ref" else ver.get("git_ref"))
    )
    source = {"via": via, "change": change, "commit": sha}
    problems = [p.to_dict() for p in rep.problems]
    fatal = rep.cannot_run or any(p.severity == ERROR for p in rep.problems)
    out_rows: list[dict[str, Any]] = []
    for row in rep.consumers:
        out: dict[str, Any] = {
            "consumer": row["consumer"], "contract": row["contract"], "label": row["label"],
            "error": row["error"], "action": SKIPPED, "question": None, "current": None,
            "state_dir": None, "warning": None,
        }  # fmt: skip
        out_rows.append(out)
        if row["error"] is not None:
            continue
        wp = ws.project(row["consumer"])
        if wp is None:  # not reachable: run_impact rows come from the manifest's links
            out["warning"] = {"code": "unknown_project", "message": row["consumer"]}
            continue
        loc = locate(wp.path, wp.name)
        out["state_dir"] = str(loc.state_dir) if loc.state_dir is not None else None
        warns = list(loc.warnings)
        try:
            if loc.state == "disabled":
                raise LinkedError("consumer_state_disabled", f"{wp.name}: [state] enabled = "
                                  "false in its studio.toml")  # fmt: skip
            assert loc.state_dir is not None
            res = deliver(loc.state_dir, ws.name, row, cand, source, dry_run=dry_run,
                          wait=lock_wait)  # fmt: skip
            out.update(res)
        except LinkedError as e:
            out["action"] = UNDELIVERED
            if e.code == "consumer_state_missing":
                e.message = (
                    f"{wp.name} has no state directory ({loc.state_dir}); its owner "
                    f"creates it with `lattice contracts questions init {wp.name}`"
                )
            warns.append(_warning(e))
        if warns:
            out["warning"] = warns[-1] if out["action"] == UNDELIVERED else warns[0]
    counts = {a: sum(1 for r in out_rows if r["action"] == a) for a in ACTIONS}
    if fatal:
        code = 2
    elif any(r["action"] == UNDELIVERED or r["error"] is not None for r in out_rows):
        code = 1
    else:
        code = 0
    return {
        "format": FORMAT, "workspace": rep.workspace, "provider": provider, "candidate": cand,
        "source": source, "advisory": True, "dry_run": dry_run, "consumers": out_rows,
        "counts": counts, "problems": problems, "exit_code": code,
        "elapsed_s": round(time.monotonic() - t0, 3),
    }  # fmt: skip


def print_notify(doc: Mapping[str, Any]) -> None:
    cand = doc["candidate"]
    ver = cand.get("version")
    shown = (f"{cand['kind']}, {_short(ver)} {str(ver['spec_hash'])[:12]}" if ver
             else f"{cand['kind']}, not loaded")  # fmt: skip
    mode = "dry run, nothing written" if doc["dry_run"] else "advisory"
    print(f"notify {doc['provider']} candidate {cand['input']} ({shown}): {mode}")
    for r in doc["consumers"]:
        label = (r["label"] or "error").upper()
        extra = ""
        if r["question"]:
            extra = f"  {r['question']}"
        if r["error"] is not None:
            extra = f"  [{r['error']['code']}] {r['error']['message']}"
        if r["warning"] is not None:
            extra += f"  [{r['warning']['code']}] {r['warning']['message']}"
        print(f"{r['consumer']}  [{r['contract']}]  {label}  {r['action']}{extra}")
    for p in doc["problems"]:
        print(f"  {p['severity']}: [{p['code']}] {p['message']}")
    n = doc["counts"]
    print(f"{len(doc['consumers'])} consumer(s): " + ", ".join(f"{n[a]} {a}" for a in ACTIONS))
