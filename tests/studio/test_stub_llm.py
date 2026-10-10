# ruff: noqa: F811 -- tests take the imported `serve` fixture as a parameter
"""The TEST-ONLY stub LLM of the Studio server (``--stub-llm`` / ``[llm] backend = "stub"``,
lattice.studio.server.StubGenerator), ``GET /api/changes/{id}?wait=``, and a change
rejected while it drafts (the dogfood check's finding, docs/results/2026-10-10-dogfood-
results.md). The dogfood check itself is dogfood/studio/check.sh."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from lattice.studio import server as server_mod
from lattice.studio.server import StubGenerator, Studio
from tests.spec.incremental_toy import NOTES_V1, NOTES_V2
from tests.studio.test_server import (
    V2_DRAFT,
    GatedGenerator,
    edit_reply,
    git,
    make_project,
    serve,  # noqa: F401 -- a fixture
)


def test_the_stub_drafts_a_change_to_ready_and_accept_writes_it(tmp_path: Path, serve) -> None:
    root = make_project(tmp_path / "p")
    s = serve(root, StubGenerator())
    _, out = s.post("/api/changes", {"request": "Notes  can be\nstarred."})
    status, c = s.get(f"/api/changes/{out['id']}?wait=30")
    assert status == 200 and c["status"] == "ready", c["error"]
    assert c["rules"] == {"added": ["R7"], "changed": [], "removed": []}
    assert c["checks"]["valid"] and c["checks"]["problems"] == []
    assert c["questions"] == [], "the carried-over question is kept, none is added"
    status, acc = s.post(f"/api/changes/{out['id']}/accept")
    assert status == 200, acc
    assert (root / "REQUIREMENTS.md").read_text() == NOTES_V1 + "- Notes can be starred.\n"
    assert git(root, "show", "--name-only", "--format=", "HEAD").split() == [
        "REQUIREMENTS.md",
        "spec.trace.json",
    ], "the stub keeps spec.py as it was"
    _, m = s.get("/api/model")
    r7 = next(r for r in m["rules"] if r["id"] == "R7")
    assert r7["status"] == "context"


def test_the_stub_drafts_a_follow_up_on_the_parent(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p"), StubGenerator())
    s.post("/api/changes", {"request": "Notes can be starred."})
    assert s.get("/api/changes/c1?wait=30")[1]["status"] == "ready"
    s.post("/api/changes", {"request": "Starred notes come first.", "parent": "c1"})
    _, c2 = s.get("/api/changes/c2?wait=30")
    assert c2["status"] == "ready" and c2["rules"]["added"] == ["R8"]
    assert s.get("/api/changes/c1")[1]["superseded_by"] == "c2"


def test_the_stub_fails_on_request(tmp_path: Path, serve) -> None:
    gen = StubGenerator()
    s = serve(make_project(tmp_path / "p"), gen)
    s.post("/api/changes", {"request": "stub:fail please"})
    _, c = s.get("/api/changes/c1?wait=30")
    assert c["status"] == "failed" and "no document between" in c["error"]
    assert len(gen.calls) == 2, "the edit and its one repair"


def test_the_stub_holds_a_change_drafting(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p"), StubGenerator())
    t0 = time.monotonic()
    s.post("/api/changes", {"request": "Notes can be pinned. stub:hold=0.5"})
    assert s.get("/api/changes/c1")[1]["status"] == "drafting"
    assert s.get("/api/changes/c1?wait=30")[1]["status"] == "ready"
    assert time.monotonic() - t0 >= 0.5


def test_the_stub_answers_ask_not_specified(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p"), StubGenerator())
    status, a = s.post("/api/ask", {"question": "Can ben read ann's note?"})
    assert status == 200, a
    assert a["kind"] == "not_specified" and a["citations"] == [] and a["verified"] is None


def test_the_stub_refuses_a_prompt_it_does_not_know() -> None:
    with pytest.raises(RuntimeError, match="does not know"):
        StubGenerator().generate("some other system prompt", [{"role": "user", "content": "x"}])


def test_backend_stub_in_studio_toml_selects_the_stub(tmp_path: Path) -> None:
    root = make_project(tmp_path / "p", repo=False)
    (root / "studio.toml").write_text('[llm]\nbackend = "stub"\n\n[rebuild]\nrepo = "../.."\n')
    assert isinstance(Studio(root).generator, StubGenerator)
    (root / "studio.toml").write_text('[llm]\nmodel = "haiku"\n')
    assert not isinstance(Studio(root).generator, StubGenerator), "never the default"


def test_the_stub_llm_flag_selects_the_stub(tmp_path: Path, monkeypatch) -> None:
    seen = {}

    def fake_make_server(root, port, **kw):
        seen.update(kw, port=port)
        raise SystemExit(0)

    monkeypatch.setattr(server_mod, "make_server", fake_make_server)
    root = make_project(tmp_path / "p", repo=False)
    with pytest.raises(SystemExit):
        server_mod.main([str(root), "--port", "0", "--stub-llm"])
    assert isinstance(seen["generator"], StubGenerator)
    with pytest.raises(SystemExit):
        server_mod.main([str(root), "--port", "0"])
    assert seen["generator"] is None


def test_unknown_studio_toml_tables_are_ignored(tmp_path: Path, serve) -> None:
    root = make_project(tmp_path / "p")
    (root / "studio.toml").write_text(
        '[llm]\nmodel = "x"\n\n[rebuild]\nrepo = "../.."\ngates = ["./check.sh"]\n'
    )
    status, m = serve(root).get("/api/model")
    assert status == 200 and m["app"]["configured"] is False


# ---------------------------------------------------------------- ?wait=


def test_wait_holds_until_the_job_has_finished(tmp_path: Path, serve) -> None:
    gate = threading.Event()
    s = serve(make_project(tmp_path / "p"), GatedGenerator([edit_reply(NOTES_V2), V2_DRAFT], gate))
    s.post("/api/changes", {"request": "allow 5 notes"})
    t0 = time.monotonic()
    status, c = s.get("/api/changes/c1?wait=0.3")
    assert status == 200 and c["status"] == "drafting" and time.monotonic() - t0 >= 0.3
    threading.Timer(0.2, gate.set).start()
    status, c = s.get("/api/changes/c1?wait=30")
    assert c["status"] == "ready", c["error"]
    t0 = time.monotonic()
    assert s.get("/api/changes/c1?wait=30")[1]["status"] == "ready"
    assert time.monotonic() - t0 < 5, "a finished job answers at once"


def test_wait_must_be_seconds_from_0_to_60(tmp_path: Path, serve) -> None:
    s = serve(make_project(tmp_path / "p"), StubGenerator())
    s.post("/api/changes", {"request": "Notes can be starred."})
    for bad in ("soon", "-1", "61", "nan", "inf"):
        status, err = s.get(f"/api/changes/c1?wait={bad}")
        assert status == 422 and err["detail"].startswith("wait must be"), bad
    assert s.get("/api/changes/c9?wait=1")[0] == 404


def test_wait_on_a_cancelled_change_answers_after_its_job_stops(tmp_path: Path, serve) -> None:
    gate = threading.Event()
    s = serve(make_project(tmp_path / "p"), GatedGenerator([edit_reply(NOTES_V2), V2_DRAFT], gate))
    s.post("/api/changes", {"request": "allow 5 notes"})
    assert s.delete("/api/changes/c1")[0] == 200
    threading.Timer(0.2, gate.set).start()
    _, c = s.get("/api/changes/c1?wait=30")
    assert c["status"] == "cancelled" and "total_s" in c["timings"]


# ---------------------------------------------------------------- the dogfood finding


def test_a_change_rejected_while_drafting_stays_rejected(tmp_path: Path, serve) -> None:
    """Before the fix, the drafting job overwrote the rejection: the change came back
    `ready`, and accepting it committed a proposal the owner had rejected."""
    root = make_project(tmp_path / "p")
    gate = threading.Event()
    s = serve(root, GatedGenerator([edit_reply(NOTES_V2), V2_DRAFT], gate))
    s.post("/api/changes", {"request": "allow 5 notes"})
    assert s.post("/api/changes/c1/reject") == (200, {"ok": True})
    gate.set()
    _, c = s.get("/api/changes/c1?wait=30")
    assert c["status"] == "rejected"
    status, err = s.post("/api/changes/c1/accept")
    assert status == 409 and err["detail"] == "change c1 is rejected, not ready"
    assert (root / "REQUIREMENTS.md").read_text() == NOTES_V1
