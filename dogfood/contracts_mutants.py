"""The mutation run of layer 3 (contracts between projects), pre-registered with
``docs/results/2026-10-13-contracts-prereg.md`` section 6.

Modelled on ``dogfood/multi_mutants.py``: for each mutant the sources and the suite are copied to
a scratch directory and the mutant's one-line edit is applied to the COPY (its old text must
occur exactly once in the CURRENT source: a stale patch fails loudly, it is never skipped), then
the suite runs there with the copy first on ``PYTHONPATH``. A mutant is KILLED when pytest runs
and reports a failing test; a mutant that does not compile, or a run that ends in a collection or
import error (no ``FAILED`` line), is an ERROR and never counts as a kill. A control run of the
unpatched copy comes first and must pass. The real tree is never written to (the hashes of every
mutated file are checked again at the end).

The eleven mutants and the probes the prereg predicted to kill each (D, L, W, C, V, B: the
prereg's probe series; the tests are in ``tests/contracts``):

* CM1  status ignored: ``status_not_observed`` never emitted             (D2, L2)
* CM2  auth ignored: ``auth``/``as_user`` never compared                 (D5, L3)
* CM3  ``endpoint_missing`` treated as satisfied                         (D1)
* CM4  pin ignored: P := E (every unsatisfied reliance is a gap)         (D1-D5, D6, D9, C4)
* CM5  opt-in ignored: every link is checked whatever ``contract`` says  (W1)
* CM6  field reliance ignored (request and response)                     (D3, D4, L4)
* CM7  ids not templated (concrete paths kept)                           (B0)
* CM8  ``spec_hash`` over the spec file bytes, not the canonical export  (C2, C3)
* CM9  ``pin`` writes despite BROKEN                                     (V4)
* CM10 live: the ``ensure_safe_target`` call removed                     (L5)
* CM11 ``Link.to_dict`` always emits ``contract``                        (W5)

The twelve mutants of the route exclusion (``docs/results/2026-10-13-contracts-exclude-prereg.md``
section 4; the tests are ``tests/contracts/test_exclude.py``, the probes named are
``dogfood/contracts/exclude_probes.py``):

* XM1  filter not applied: every endpoint kept, ``excluded`` still set   (X1, X2, X9b)
* XM2  filter after hashing: ``spec_hash`` over the unfiltered export    (X1)
* XM3  normalisation skipped: raw strings stored and compared            (X2b, X4c)
* XM4  validation off: bad entries dropped, no problem                   (X5)
* XM5  duplicate check removed                                           (X5 row 5)
* XM6  excluded imports treated as satisfied (``evaluate`` skips them)   (X3)
* XM7  ``excluded`` not recorded in the export                           (X4d, X6b)
* XM8  diff ignores ``excluded``: ``endpoint_removed``, no ``exclusion_added``  (X4a, X1)
* XM9  live replays excluded imports                                     (X9b)
* XM10 ``exclude_unmatched`` never emitted                               (X6a, X4d)
* XM11 ``WsProject.to_dict`` always emits ``exclude``                    (X8)
* XM12 an excluded import is BROKEN only when the pin held it            (X3 pinned after, X9b)

(XM12 is the prereg's draft rule, amended before the build; it is listed after XM11.) The
prereg's table says "11 of 11" and lists twelve rows: all twelve are run, 11 + 12 = 23.

The fourteen mutants of the cross-project impact preview
(``docs/results/2026-10-13-contracts-impact-prereg.md`` section 4; every patch is a change of
``lattice/contracts/impact.py``, the tests are ``tests/contracts/test_impact.py``, the probes
named are ``dogfood/contracts/impact_probes.py``):

* IMP1  candidate ignored: the current export used as the candidate        (I1 D1-D5)
* IMP2  BROKEN mapped to ``regression``                                    (I1 D1-D5)
* IMP3  a relied CHANGED mapped to ``contradiction``                       (I1 D8, D9)
* IMP4  ``relied`` ignored: any CHANGED is a ``regression``                (I1 D6, I3)
* IMP5  baseline = the current export, not the pin                         (I0c)
* IMP6  ``exclude`` not passed for the candidate                           (I3x)
* IMP7  the manifest's ``spec`` ignored for the candidate                  (I0a)
* IMP8  links without ``contract`` listed                                  (I4-f)
* IMP9  rows shuffled                                                      (I4, 10-run identity)
* IMP10 the candidate's entry written into the consumer's lock             (I6, I1 run 2)
* IMP11 exit 1 on any ``regression``                                       (I1 D8, D9 exit)
* IMP12 the ref materialised by ``git checkout`` in the provider and back  (I5-h, I6)
* IMP13 a ``no_pin`` row reported ``unaffected``                           (I4-g)
* IMP14 the temporary directory not removed                                (I6 (2))

The eighteen mutants of linked pending questions in consumers (slice 4b,
``docs/results/2026-10-14-linked-questions-prereg.md`` section 4; the patches are changes of
``lattice/contracts/linked.py``, ``lattice/studio/server.py`` and ``lattice/mcp/projects.py``, the
tests are ``tests/contracts/test_linked.py``, ``tests/studio/test_linked_api.py`` and
``tests/mcp/test_linked_questions.py``, the probes named are in
``dogfood/contracts/linkedq_probes.py``):

* LQ1  a question for every label                                          (Q0, Q1 job D8, D9)
* LQ2  ``regression`` also creates a question                              (Q1 D8, D9, D10, D13)
* LQ3  key check skipped (no idempotence)                                  (Q2 s2)
* LQ4  the key includes the candidate's spec_hash                          (Q2 s3 d2c4)
* LQ5  the ``current`` guard dropped: a dismissed key is recreated         (Q2 s7)
* LQ6  the question saved via ``StateStore.save`` into ``changes/``        (Q3 a, b)
* LQ7  ids from ``StateStore.allocate`` (writes ``meta.json``)             (Q3 b)
* LQ8  the missing state dir created (``mkdir(parents=True)``)             (Q3 d)
* LQ9  state dir computed from the provider (its path and name)            (Q1, Q3 a)
* LQ10 ``[state] dir`` ignored                                             (Q3 e)
* LQ11 delivery run inline in ``accept`` and its exception propagated      (Q4 ii)
* LQ12 notification run synchronously in ``accept``                        (Q4 iv)
* LQ13 a clean notification sets ``status: resolved`` (auto-clear)         (Q2 s5)
* LQ14 ``atomic_write`` replaced by ``write_text``                         (Q3 f)
* LQ15 no ``flock`` around allocation                                      (Q3 g)
* LQ16 resolve looks the id up in every workspace project                  (Q5 /p/scheduler/)
* LQ17 an MCP ``linked_question_resolve`` tool is registered               (Q5 tools/list)
* LQ18 a key present in any status is never recreated (the draft's rule)   (Q2 s9)

LQ14 and LQ15 are probabilistic (races): a survivor is a finding, not a pass.

The thirteen mutants of the live check on a Go + MySQL provider
(``docs/results/2026-10-15-live-contract-go-mysql-prereg.md`` section 4; every patch is a change
of ``lattice/contracts/live.py``, the tests are ``tests/contracts/test_live_server.py``, the probes
named are in ``dogfood/contracts/livego_probes.py``, which also runs them on each mutated copy:
``livego_probes.py --mutants LG1,...``):

* LG1  ``LIVE_DATABASES`` back to sqlite only                              (G0)
* LG2  no shared server: ``databases`` not passed (a container per replay) (G0: 7 containers)
* LG3  one clone reused for every replay                                   (G0: given rows refused)
* LG4  ``LATTICE_<DIALECT>_URL`` kept in the child's environment           (Z1)
* LG5  the harness's ``database_url`` honoured                             (Z1)
* LG6  driver precheck removed                                             (A2)
* LG7  Docker precheck removed                                             (A1)
* LG8  image precheck removed                                              (A3)
* LG9  label sweep removed                                                 (K3a: container left)
* LG10 pid sweep removed                                                   (K3a: streak-api left)
* LG11 server opened eagerly (before the first replay)                     (G-D7a, G-D7c)
* LG12 ``database`` block also on SQLite                                   (B1: byte diff)
* LG13 SIGTERM grace removed (SIGKILL at once)                             (its unit test only)

All thirteen are killed by ``tests/contracts/test_live_server.py`` (fakes for Docker, the server
and the app; a real child for the SIGTERM grace and the sweep); the first stage of each is that
file.

Test selection: ``tests/contracts tests/workspace -q -x`` (about a minute); a mutant that
touches only ``live.py`` runs ``tests/contracts/test_live.py`` first and the rest only when it
survives that; an XM mutant runs ``tests/contracts/test_exclude.py`` first and the rest only when
it survives that; an IMP mutant runs ``tests/contracts/test_impact.py`` first and the rest only
when it survives that; an LQ mutant runs its own test file first (contracts, studio or mcp) and
the three linked-question test files together when it survives that. The control runs the suite
and then the studio and mcp linked-question tests. ``--all-failures`` drops ``-x`` to list
every killing test.

Usage: ``python dogfood/contracts_mutants.py [--out FILE] [--jobs N] [--all-failures] [ID ...]``.
Exit 0 when the control passes and every mutant is killed; the last line is
``mutants: N/N killed``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SUITE = ["tests/contracts", "tests/workspace"]
LIVE_ONLY = ["tests/contracts/test_live.py"]
EXCLUDE_ONLY = ["tests/contracts/test_exclude.py"]
IMPACT_ONLY = ["tests/contracts/test_impact.py"]
LQ_CONTRACTS = ["tests/contracts/test_linked.py"]
LQ_STUDIO = ["tests/studio/test_linked_api.py"]
LQ_MCP = ["tests/mcp/test_linked_questions.py"]
LQ_ALL = [*LQ_CONTRACTS, *LQ_STUDIO, *LQ_MCP]
LIVE_SERVER_ONLY = ["tests/contracts/test_live_server.py"]
CK, EX, LV, WS, IM = (
    "lattice/contracts/check.py",
    "lattice/contracts/export.py",
    "lattice/contracts/live.py",
    "lattice/workspace.py",
    "lattice/contracts/impact.py",
)
LK, SV, PJ = (
    "lattice/contracts/linked.py",
    "lattice/studio/server.py",
    "lattice/mcp/projects.py",
)
# copied into the scratch tree (dogfood/contracts only: the fixtures, not another node's probes)
COPY = [
    "lattice",
    "pyproject.toml",
    "tests",
    "dogfood/contracts/ci-bot",
    "dogfood/contracts/golden",
    "dogfood/contracts/golden-streak",
    "dogfood/contracts/streak-widget",
]

# id -> (behaviour, file, old text, new text, first-stage tests or None)
MUTANTS: dict[str, tuple[str, str, str, str, list[str] | None]] = {
    "CM1": (
        "status ignored: status_not_observed is never emitted",
        CK,
        '        return int(item) in ep["statuses"]\n',
        "        return True  # MUTANT CM1: a status claim always holds\n",
        None,
    ),
    "CM2": (
        "auth ignored: auth / as_user never compared",
        CK,
        '    if kind == "auth":\n        if item == "public":\n',
        '    if kind == "auth":\n'
        "        return True  # MUTANT CM2: an auth claim always holds\n"
        '        if item == "public":\n',
        None,
    ),
    "CM3": (
        "endpoint_missing treated as satisfied",
        CK,
        '    if ep is None:\n        return False\n    if kind == "endpoint":\n',
        "    if ep is None:\n"
        '        return kind == "endpoint"  # MUTANT CM3: missing is satisfied\n'
        '    if kind == "endpoint":\n',
        None,
    ),
    "CM4": (
        "pin ignored: P := E (every unsatisfied reliance is a gap, no CHANGED)",
        CK,
        "    pinned_v = version_of(entry)\n",
        "    pinned_v = version_of(entry)\n"
        '    entry = {**entry, "export": cur["export"]}  # MUTANT CM4: P := E\n',
        None,
    ),
    "CM5": (
        "opt-in ignored: every link is checked whatever contract says",
        CK,
        "    return [lk for lk in ws.links if lk.contract and (consumer is None or "
        "lk.source == consumer)]\n",
        "    return [lk for lk in ws.links if (consumer is None or lk.source == consumer)]"
        "  # MUTANT CM5\n",
        None,
    ),
    "CM6": (
        "field reliance ignored (request and response)",
        CK,
        "        for kind, item in claims_of(imp):\n",
        "        for kind, item in (c for c in claims_of(imp) if not c[0].endswith('_field')):"
        "  # MUTANT CM6\n",
        None,
    ),
    "CM7": (
        "ids not templated (concrete paths kept)",
        EX,
        "        if _INT.fullmatch(seg) or _UUID.fullmatch(seg) or _NAMED.fullmatch(seg):\n",
        "        if _NAMED.fullmatch(seg):  # MUTANT CM7: digits and UUIDs are kept\n",
        None,
    ),
    "CM8": (
        "spec_hash over the spec file bytes, not the canonical export",
        EX,
        '        "version": {"dirty": dirty, "git_ref": git_ref, '
        '"spec_hash": spec_hash(export)},\n',
        '        "version": {"dirty": dirty, "git_ref": git_ref, "spec_hash": hashlib.sha256('
        'Path(sys.modules[ref.split(":", 1)[0]].__file__).read_bytes()).hexdigest()},'
        "  # MUTANT CM8\n",
        None,
    ),
    "CM9": (
        "pin writes despite BROKEN",
        CK,
        '            "status": "refused" if broken and not force else "pinned", "error": None,\n',
        '            "status": "pinned", "error": None,  # MUTANT CM9: BROKEN does not refuse\n',
        None,
    ),
    "CM10": (
        "live: the ensure_safe_target call removed",
        LV,
        "    try:\n        ensure_safe_target(host)\n    except UnsafeTargetError as e:\n",
        "    try:\n        pass  # MUTANT CM10: no loopback guard\n"
        "    except UnsafeTargetError as e:\n",
        LIVE_ONLY,
    ),
    "CM11": (
        "Link.to_dict always emits contract",
        WS,
        '        if self.contract is not None:\n            out["contract"] = self.contract\n',
        '        out["contract"] = self.contract  # MUTANT CM11: emitted even when None\n',
        None,
    ),
    "XM1": (
        "exclude: the filter is not applied (every endpoint kept, excluded still set)",
        EX,
        '    out["endpoints"] = {k: v for k, v in held.items() if k not in keys}\n',
        '    out["endpoints"] = dict(held)  # MUTANT XM1: nothing is dropped\n',
        EXCLUDE_ONLY,
    ),
    "XM2": (
        "exclude: spec_hash over the unfiltered export (filter after hashing)",
        EX,
        '        "version": {"dirty": dirty, "git_ref": git_ref, '
        '"spec_hash": spec_hash(export)},\n',
        '        "version": {"dirty": dirty, "git_ref": git_ref, "spec_hash": spec_hash('
        "export_spec(load_spec(project, ref)))},  # MUTANT XM2: hashed before the filter\n",
        EXCLUDE_ONLY,
    ),
    "XM3": (
        "exclude: normalisation skipped (raw strings stored and compared)",
        EX,
        "            keys.append(parse_endpoint(entry))\n",
        "            parse_endpoint(entry)\n"
        "            keys.append(entry)  # MUTANT XM3: the raw string is kept\n",
        EXCLUDE_ONLY,
    ),
    "XM4": (
        "exclude: validation off (bad entries dropped, no problem)",
        WS,
        '            keys, reasons = check_exclude(item["exclude"])\n',
        '            keys, reasons = check_exclude(item["exclude"])[0], []  # MUTANT XM4\n',
        EXCLUDE_ONLY,
    ),
    "XM5": (
        "exclude: the duplicate check removed",
        EX,
        "    for key in dict.fromkeys(k for k in keys if keys.count(k) > 1):\n",
        "    for key in ():  # MUTANT XM5: a key named twice is accepted\n",
        EXCLUDE_ONLY,
    ),
    "XM6": (
        "exclude: an import of an excluded endpoint is treated as satisfied",
        CK,
        "        if imp.endpoint in excluded:\n",
        "        if imp.endpoint in excluded:\n"
        "            continue  # MUTANT XM6: evaluate skips it\n",
        EXCLUDE_ONLY,
    ),
    "XM7": (
        "exclude: excluded is not recorded in the export",
        EX,
        '    out["excluded"] = keys\n',
        "    pass  # MUTANT XM7: the declaration is not kept\n",
        EXCLUDE_ONLY,
    ),
    "XM8": (
        "exclude: the diff ignores excluded (endpoint_removed, no exclusion_added)",
        CK,
        '    p_ex, c_ex = set(pinned.get("excluded", ())), set(current.get("excluded", ()))\n',
        "    p_ex, c_ex = set(), set()  # MUTANT XM8: excluded is not diffed\n",
        EXCLUDE_ONLY,
    ),
    "XM9": (
        "exclude: live replays an excluded import",
        LV,
        "    replayable = [imp for imp in imports if imp.endpoint not in excluded]\n",
        "    replayable = list(imports)  # MUTANT XM9: the excluded are replayed too\n",
        EXCLUDE_ONLY,
    ),
    "XM10": (
        "exclude: exclude_unmatched is never emitted",
        CK,
        '        self.unmatched.update((name, k) for k in got.get("exclude_unmatched", ()))\n',
        "        pass  # MUTANT XM10: no warning\n",
        EXCLUDE_ONLY,
    ),
    "XM11": (
        "exclude: WsProject.to_dict always emits exclude ([] when None)",
        WS,
        '        out["exclude"] = None if self.exclude is None else list(self.exclude)\n',
        '        out["exclude"] = [] if self.exclude is None else list(self.exclude)'
        "  # MUTANT XM11\n",
        EXCLUDE_ONLY,
    ),
    "XM12": (
        "exclude: an excluded import is BROKEN only when the pin held it (the draft rule)",
        CK,
        "        if imp.endpoint in excluded:\n",
        '        if imp.endpoint in excluded and holds(seen, imp, "endpoint", ""):'
        "  # MUTANT XM12\n",
        EXCLUDE_ONLY,
    ),
    "IMP1": (
        "impact: the candidate is ignored (the current export is the candidate)",
        IM,
        '    c_exp, c_ver = cand["export"], cand["version"]\n',
        '    c_exp, c_ver = (cur or cand)["export"], cand["version"]  # MUTANT IMP1\n',
        IMPACT_ONLY,
    ),
    "IMP2": (
        "impact: a BROKEN reliance is labelled regression",
        IM,
        "    if broken:\n        return CONTRADICTION\n",
        "    if broken:\n        return REGRESSION  # MUTANT IMP2\n",
        IMPACT_ONLY,
    ),
    "IMP3": (
        "impact: a relied CHANGED is labelled contradiction",
        IM,
        '    if any(f.get("relied") for f in changed):\n        return REGRESSION\n',
        '    if any(f.get("relied") for f in changed):\n'
        "        return CONTRADICTION  # MUTANT IMP3\n",
        IMPACT_ONLY,
    ),
    "IMP4": (
        "impact: relied is ignored (any CHANGED is a regression)",
        IM,
        '    if any(f.get("relied") for f in changed):\n',
        "    if changed:  # MUTANT IMP4\n",
        IMPACT_ONLY,
    ),
    "IMP5": (
        "impact: the baseline is the current export, not the pin",
        IM,
        "    pinned_v = ck.version_of(entry)\n",
        "    pinned_v = ck.version_of(entry)\n"
        '    entry = {**entry, "export": (cur or cand)["export"]}  # MUTANT IMP5\n',
        IMPACT_ONLY,
    ),
    "IMP6": (
        "impact: exclude is not passed for the candidate",
        IM,
        "                got = compute_export(temp, spec_ref, exclude)\n",
        "                got = compute_export(temp, spec_ref, ())  # MUTANT IMP6\n",
        IMPACT_ONLY,
    ),
    "IMP7": (
        "impact: the manifest's spec is ignored for the candidate",
        IM,
        "                got = compute_export(temp, spec_ref, exclude)\n",
        "                got = compute_export(temp, DEFAULT_SPEC, exclude)  # MUTANT IMP7\n",
        IMPACT_ONLY,
    ),
    "IMP8": (
        "impact: links without a contract key are listed",
        IM,
        "if lk.target == provider and lk.contract),",
        "if lk.target == provider),  # MUTANT IMP8",
        IMPACT_ONLY,
    ),
    "IMP9": (
        "impact: the rows are shuffled",
        IM,
        "        session = ck._Session(ws)\n",
        "        session = ck._Session(ws)\n"
        "        __import__('random').shuffle(links)  # MUTANT IMP9\n",
        IMPACT_ONLY,
    ),
    "IMP10": (
        "impact: the candidate's entry is written into the consumer's lock",
        IM,
        "    pinned_v = ck.version_of(entry)\n",
        "    pinned_v = ck.version_of(entry)\n"
        '    new = {"spec_ref": cand["spec_ref"], "version": cand["version"],\n'
        '           "export": cand["export"]}  # MUTANT IMP10\n'
        '    ck.write_lock(lock_path, {**lock, "providers": {**lock["providers"],\n'
        "                                                   link.target: new}})\n",
        IMPACT_ONLY,
    ),
    "IMP11": (
        "impact: exit 1 on any regression",
        IM,
        '        if any(row["label"] == CONTRADICTION for row in self.consumers):\n',
        '        if any(row["label"] in (CONTRADICTION, REGRESSION) for row in self.consumers):'
        "  # MUTANT IMP11\n",
        IMPACT_ONLY,
    ),
    "IMP12": (
        "impact: the ref is checked out in the provider's tree and back",
        IM,
        "            materialise(project, sha, temp)\n",
        '            for to in (sha, "-"):  # MUTANT IMP12\n'
        '                subprocess.run(["git", "-C", str(project), "checkout", "-q", to],\n'
        "                               capture_output=True, check=False)\n"
        "            materialise(project, sha, temp)\n",
        IMPACT_ONLY,
    ),
    "IMP13": (
        "impact: a no_pin row is reported unaffected",
        IM,
        "                out.consumers.append(_error_row(link, e))\n",
        "                row = _error_row(link, e)  # MUTANT IMP13\n"
        '                if e.code == "no_pin":\n'
        "                    row.update(label=UNAFFECTED, error=None)\n"
        "                out.consumers.append(row)\n",
        IMPACT_ONLY,
    ),
    "IMP14": (
        "impact: the temporary directory is not removed",
        IM,
        "            shutil.rmtree(temp, ignore_errors=True)\n",
        "            pass  # MUTANT IMP14: left behind\n",
        IMPACT_ONLY,
    ),
    "LQ1": (
        "linked: a question for every label",
        LK,
        '    if row["label"] != imp.CONTRADICTION:\n',
        "    if False:  # MUTANT LQ1: every label creates a question\n",
        LQ_CONTRACTS,
    ),
    "LQ2": (
        "linked: a regression also creates a question",
        LK,
        '    if row["label"] != imp.CONTRADICTION:\n',
        '    if row["label"] not in (imp.CONTRADICTION, imp.REGRESSION):  # MUTANT LQ2\n',
        LQ_CONTRACTS,
    ),
    "LQ3": (
        "linked: the key check is skipped (no idempotence)",
        LK,
        '    key = key_of(row["provider"], row["consumer"], row["contract"], row)\n',
        '    key = key_of(row["provider"], row["consumer"], row["contract"], row)\n'
        "    key += str(time.time_ns())  # MUTANT LQ3: never the same key twice\n",
        LQ_CONTRACTS,
    ),
    "LQ4": (
        "linked: the key includes the candidate's spec_hash",
        LK,
        '    key = key_of(row["provider"], row["consumer"], row["contract"], row)\n',
        '    key = key_of(row["provider"], row["consumer"], row["contract"], row)\n'
        '    key += str((candidate.get("version") or {}).get("spec_hash"))  # MUTANT LQ4\n',
        LQ_CONTRACTS,
    ),
    "LQ5": (
        "linked: the `current` guard dropped (a dismissed key is recreated)",
        LK,
        '    if qid is None and cur in by_id and by_id[cur].get("key") == key:'
        "  # the state is unchanged\n",
        "    if False:  # MUTANT LQ5: the unchanged-state guard is gone\n",
        LQ_CONTRACTS,
    ),
    "LQ6": (
        "linked: the question saved through StateStore.save into changes/",
        LK,
        "                _put(path, doc)\n",
        '                if path.name.startswith("lq"):  # MUTANT LQ6\n'
        "                    from lattice.studio.state import StateStore\n\n"
        '                    StateStore(Path(state_dir)).save("changes", path.stem, doc, {})\n'
        "                else:\n"
        "                    _put(path, doc)\n",
        LQ_CONTRACTS,
    ),
    "LQ7": (
        "linked: ids from StateStore.allocate (writes meta.json)",
        LK,
        '        qid, action = f"lq{top + 1}", CREATED\n',
        "        from lattice.studio.state import StateStore  # MUTANT LQ7\n\n"
        "        qid, action = f\"lq{StateStore(qdir.parent).allocate('changes')}\", CREATED\n",
        LQ_CONTRACTS,
    ),
    "LQ8": (
        "linked: the missing state dir is created (mkdir parents)",
        LK,
        "        path.mkdir(exist_ok=True)  # one level: the state directory must already exist\n",
        "        path.mkdir(parents=True, exist_ok=True)  # MUTANT LQ8\n",
        LQ_CONTRACTS,
    ),
    "LQ9": (
        "linked: the state dir is the provider's, not the consumer's",
        LK,
        "        loc = locate(wp.path, wp.name)\n",
        "        loc = locate(ws.project(provider).path, provider)  # MUTANT LQ9\n",
        LQ_CONTRACTS,
    ),
    "LQ10": (
        "linked: [state] dir of the consumer's studio.toml is ignored",
        LK,
        "    cands = list(dict.fromkeys(state_dir_path(project, n, state_t, rebuild_t) "
        "for n in names))\n",
        "    cands = list(dict.fromkeys(state_dir_path(project, n, {}, rebuild_t) "
        "for n in names))  # MUTANT LQ10\n",
        LQ_CONTRACTS,
    ),
    "LQ11": (
        "studio: the delivery runs inline in accept and its exception propagates",
        SV,
        "            self._linked_jobs.put(entry)\n"
        "        except Exception as exc:  # noqa: BLE001 -- advisory: never reaches the accept\n",
        "            self._linked_deliver(entry)  # MUTANT LQ11: inline, exceptions propagate\n"
        "        except OSError as exc:\n",
        LQ_STUDIO,
    ),
    "LQ12": (
        "studio: the notification is delivered synchronously in accept",
        SV,
        "            self._linked_jobs.put(entry)\n",
        "            self._linked_deliver(entry)  # MUTANT LQ12: synchronous\n",
        LQ_STUDIO,
    ),
    "LQ13": (
        "linked: a clean notification resolves the open questions (auto-clear)",
        LK,
        "            puts.append((qdir / LINKS / lname, "
        "_link_doc(row, None, candidate, source)))\n",
        "            puts.append((qdir / LINKS / lname, "
        "_link_doc(row, None, candidate, source)))\n"
        "            for n, _x, d in rows:  # MUTANT LQ13\n"
        '                if d and d.get("status") == OPEN:\n'
        '                    puts.append((qdir / f"lq{n}.json", {**d, "status": "resolved"}))\n',
        LQ_CONTRACTS,
    ),
    "LQ14": (
        "linked: atomic_write replaced by write_text (torn reads possible)",
        LK,
        '    atomic_write(path, json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8"))\n',
        '    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")'
        "  # MUTANT LQ14\n",
        LQ_CONTRACTS,
    ),
    "LQ15": (
        "linked: no flock around allocation",
        LK,
        "                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n",
        "                pass  # MUTANT LQ15: no lock\n",
        LQ_CONTRACTS,
    ),
    "LQ16": (
        "studio: clearing looks the id up in every workspace project's state",
        SV,
        "        d = self._linked_dir()\n"
        '        status = linked.RESOLVED if verb == "resolve" else linked.DISMISSED\n',
        "        d = self._linked_dir()\n"
        '        status = linked.RESOLVED if verb == "resolve" else linked.DISMISSED\n'
        "        from lattice import workspace as wsmod  # MUTANT LQ16\n\n"
        "        other, _p = wsmod.try_load(self.workspace_path)\n"
        "        for wp in other.projects if other is not None else []:\n"
        "            loc = linked.locate(wp.path, wp.name)\n"
        '            if loc.state_dir and (loc.state_dir / linked.DIR / f"{qid}.json").is_file():\n'
        "                d = loc.state_dir\n"
        "                break\n",
        LQ_STUDIO,
    ),
    "LQ17": (
        "mcp: a linked_question_resolve tool is registered",
        PJ,
        '            linked_question_list,\n            required=("consumer",),\n        )\n',
        "            linked_question_list,\n"
        '            required=("consumer",),\n'
        "        )\n"
        "        self._reg(  # MUTANT LQ17\n"
        '            "linked_question_resolve",\n'
        '            "resolve a linked question",\n'
        '            {"consumer": {"type": "string"}, "id": {"type": "string"}},\n'
        '            lambda a: {"ok": True},\n'
        '            required=("consumer", "id"),\n'
        "        )\n",
        LQ_MCP,
    ),
    "LQ18": (
        "linked: a key present in any status is never recreated (the draft's rule)",
        LK,
        '        if doc is not None and doc.get("key") == key and doc.get("status") == OPEN:\n',
        '        if doc is not None and doc.get("key") == key:  # MUTANT LQ18\n',
        LQ_CONTRACTS,
    ),
    "LG1": (
        "live go: LIVE_DATABASES back to sqlite only (mysql is unsupported_database)",
        LV,
        'LIVE_DATABASES = ("sqlite", "mysql")\n',
        'LIVE_DATABASES = ("sqlite",)  # MUTANT LG1\n',
        LIVE_SERVER_ONLY,
    ),
    "LG2": (
        "live go: no shared server (databases not passed: a container per replay)",
        LV,
        '    extra = {} if databases is None else {"databases": databases}\n',
        "    extra = {}  # MUTANT LG2: every replay opens its own server\n",
        LIVE_SERVER_ONLY,
    ),
    "LG3": (
        "live go: one clone reused for every replay",
        LV,
        "        return self._dbs\n",
        "        dbs = self._dbs  # MUTANT LG3: the first clone for every replay\n"
        "        first: list[Any] = self.__dict__.setdefault('_one', [])\n\n"
        "        def make(variant: str, path: Any = None) -> Any:\n"
        "            if not first:\n"
        "                first.append(dbs(variant, path))\n"
        "            return first[0]\n\n"
        "        make.release = lambda ref: None  # type: ignore[attr-defined]\n"
        "        return make\n",
        LIVE_SERVER_ONLY,
    ),
    "LG4": (
        "live go: LATTICE_<DIALECT>_URL kept in the child's environment",
        LV,
        '            os.environ.pop(f"LATTICE_{name.upper()}_URL", None)\n',
        "            pass  # MUTANT LG4: the user's server stays in the environment\n",
        LIVE_SERVER_ONLY,
    ),
    "LG5": (
        "live go: the harness's database_url honoured",
        LV,
        "dbserver.server_for(self.engine, None, name_prefix=CONTAINER_PREFIX)",
        "dbserver.server_for(self.engine, getattr(self.harness, 'database_url', None),"
        " name_prefix=CONTAINER_PREFIX)  # MUTANT LG5",
        LIVE_SERVER_ONLY,
    ),
    "LG6": (
        "live go: the driver precheck removed",
        LV,
        "        missing = _missing_requirements(self.desc.requires)\n",
        "        missing: list[str] = []  # MUTANT LG6\n",
        LIVE_SERVER_ONLY,
    ),
    "LG7": (
        "live go: the Docker precheck removed",
        LV,
        "        if not dbserver.docker_available():\n",
        "        if False:  # MUTANT LG7\n",
        LIVE_SERVER_ONLY,
    ),
    "LG8": (
        "live go: the image precheck removed",
        LV,
        "        if not _image_present(image):\n",
        "        if False:  # MUTANT LG8\n",
        LIVE_SERVER_ONLY,
    ),
    "LG9": (
        "live go: the label sweep removed (a container of a killed child is left)",
        LV,
        "    if not marker.is_file() or not OWNER_RE.fullmatch(owner):\n        return\n",
        "    if True:  # MUTANT LG9: containers are never swept\n        return\n",
        LIVE_SERVER_ONLY,
    ),
    "LG10": (
        "live go: the pid sweep removed (an app of a killed child is left)",
        LV,
        "    if pids.is_file():\n",
        "    if False:  # MUTANT LG10\n",
        LIVE_SERVER_ONLY,
    ),
    "LG11": (
        "live go: the server opened eagerly, before the first replay",
        LV,
        "    outcomes: list[dict[str, Any]] = []\n",
        "    outcomes: list[dict[str, Any]] = []\n"
        "    if server is not None:  # MUTANT LG11\n"
        "        server.databases()\n",
        LIVE_SERVER_ONLY,
    ),
    "LG12": (
        "live go: the database block also on a SQLite result",
        LV,
        '    if got.get("database") is not None:  # a server dialect: the throwaway '
        "server's description\n"
        '        out["database"] = got["database"]\n',
        '    out["database"] = got.get("database") or {"engine": "sqlite"}  # MUTANT LG12\n',
        LIVE_SERVER_ONLY,
    ),
    "LG13": (
        "live go: SIGTERM grace removed (SIGKILL at once, as before)",
        LV,
        "        os.killpg(proc.pid, signal.SIGTERM)\n",
        "        os.killpg(proc.pid, signal.SIGKILL)  # MUTANT LG13\n",
        LIVE_SERVER_ONLY,
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns(
        "__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache", "node_modules", "_tmp_*"
    )
    for rel in COPY:
        src = ROOT / rel
        if not src.exists():
            raise SystemExit(f"cannot copy {rel}: it is not in {ROOT}")
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, out, ignore=ignore)
        else:
            shutil.copy2(src, out)


def apply(dst: Path, mid: str) -> None:
    _what, rel, old, new, _first = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    if old == new:
        raise SystemExit(f"{mid}: the patch changes nothing")
    mutated = text.replace(old, new)
    try:
        compile(mutated, rel, "exec")
    except SyntaxError as e:
        raise SystemExit(
            f"{mid}: the mutant does not compile ({e}); a syntax error is no kill"
        ) from e
    path.write_text(mutated, encoding="utf-8")


def _env(dst: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(dst)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _pytest_cmd() -> list[str]:
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    return ["uv", "run", "--no-sync", "--project", str(ROOT), "pytest"]


def run_pytest(dst: Path, tests: list[str], all_failures: bool) -> dict[str, Any]:
    t0 = time.monotonic()
    cmd = [*_pytest_cmd(), *tests, "-q", "-p", "no:cacheprovider", "-rf", "--no-header"]
    if not all_failures:
        cmd.append("-x")
    try:
        r = subprocess.run(
            cmd, cwd=str(dst), env=_env(dst), capture_output=True, text=True, timeout=1200
        )
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 1200s]", 124
    failed = [m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)]
    summary = next(
        (ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed|error)", ln)),
        "",
    ).strip()
    return {
        "exit": rc,
        "failed": failed,
        "summary": summary,
        "seconds": round(time.monotonic() - t0, 1),
        "tail": out[-2000:],
    }


def one(mid: str, all_failures: bool) -> dict[str, Any]:
    """Run the control (`mid` == "control") or one mutant: the first stage, then the full suite
    only when a first stage exists and the mutant survived it."""
    t0 = time.monotonic()
    first = None if mid == "control" else MUTANTS[mid][4]
    if mid == "control":
        stages: list[list[str]] = [SUITE, [*LQ_STUDIO, *LQ_MCP]]
    elif mid.startswith("LQ"):
        stages = [first, LQ_ALL] if first else [LQ_ALL]
    else:
        stages = [first, SUITE] if first else [SUITE]
    with tempfile.TemporaryDirectory(prefix=f"lattice-contracts-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res: dict[str, Any] = {}
        for tests in stages:
            res = run_pytest(dst, tests, all_failures)
            res["tests"] = " ".join(tests)
            if res["exit"] != 0:
                break
    if mid == "control":
        verdict = "PASS (control)" if res["exit"] == 0 else "CONTROL FAILED"
    elif res["exit"] == 0:
        verdict = "SURVIVED"
    elif res["failed"]:
        verdict = "killed"
    else:
        verdict = "ERROR"  # a collection/import error or a timeout: not a kill
    return {"pytest": res, "verdict": verdict, "seconds": round(time.monotonic() - t0, 1)}


def _order(mid: str) -> tuple[int, int]:
    prefix = mid.rstrip("0123456789")
    return (("CM", "XM", "IMP", "LQ", "LG").index(prefix), int(mid[len(prefix) :]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=sorted(MUTANTS, key=_order))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--all-failures", action="store_true", help="run without -x")
    args = ap.parse_args(argv)
    for mid in args.ids:
        if mid not in MUTANTS:
            raise SystemExit(f"unknown mutant {mid}")
    files = sorted({MUTANTS[m][1] for m in MUTANTS})
    hashes = {f: sha(ROOT / f) for f in files}
    print("hashes:", json.dumps(hashes), flush=True)
    with tempfile.TemporaryDirectory() as d:  # every patch applies cleanly before anything runs
        copy_tree(Path(d))
        for mid in args.ids:
            apply(Path(d), mid)
            (Path(d) / MUTANTS[mid][1]).write_text(  # undo: each patch is checked on its own
                (ROOT / MUTANTS[mid][1]).read_text(encoding="utf-8"), encoding="utf-8"
            )
    t0 = time.monotonic()
    runs: dict[str, dict[str, Any]] = {}
    control = one("control", args.all_failures)
    runs["control"] = control
    print(
        f"control: {control['verdict']} ({control['pytest']['summary']}) {control['seconds']}s",
        flush=True,
    )
    ok = control["verdict"] == "PASS (control)"
    if ok:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(one, mid, args.all_failures): mid for mid in args.ids}
            for fut in concurrent.futures.as_completed(futs):
                mid = futs[fut]
                runs[mid] = res = fut.result()
                failed = res["pytest"]["failed"]
                print(
                    f"{mid}: {res['verdict']} ({res['seconds']}s, pytest failed {len(failed)}: "
                    f"{', '.join(f.split('::', 1)[-1] for f in failed[:3])})",
                    flush=True,
                )
    else:
        print(control["pytest"]["tail"], flush=True)
    changed = [f for f in files if sha(ROOT / f) != hashes[f]]
    print(f"\n{'id':<8} {'verdict':<14} {'sec':>6}  {'change':<62} killed by (first failing test)")
    for mid in sorted(runs, key=lambda m: (-1, -1) if m == "control" else _order(m)):
        res = runs[mid]
        if mid == "control":
            what, by = "unmutated copy", res["pytest"]["summary"]
        else:
            what = MUTANTS[mid][0]
            failed = res["pytest"]["failed"]
            by = failed[0] if failed else res["verdict"]
        print(f"{mid:<8} {res['verdict']:<14} {res['seconds']:>6}  {what[:62]:<62} {by}")
    killed = sum(1 for m in args.ids if runs.get(m, {}).get("verdict") == "killed")
    if changed:
        print(f"the real tree was modified: {changed}", file=sys.stderr)
    print(f"total wall time: {round(time.monotonic() - t0, 1)}s")
    if args.out:
        args.out.write_text(json.dumps({"hashes": hashes, "runs": runs}, indent=1))
    print(f"mutants: {killed}/{len(args.ids)} killed")
    return 0 if ok and killed == len(args.ids) and not changed else 1


if __name__ == "__main__":
    sys.exit(main())
