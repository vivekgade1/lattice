"""``lattice check`` layer R on a .NET app logs in again in each probe's session (node F5): an ASP.NET Core
app signs its tokens with a key made at start-up, so a token minted in one probe's process is refused in the
next; and a 5xx to the anonymous caller came from the endpoint (authorization runs before it): ``crash_admits``.
The Python and Go call paths are unchanged (neither option unless the caller passes one)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from lattice.importer.transport import AppRuntime
from lattice.spec import probes

chk = importlib.import_module("lattice.spec.check")  # the package's `check` is the function


def _run(monkeypatch: pytest.MonkeyPatch, runtime: Any, options: dict[str, Any] | None = None) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(spec: Any, sessions: Any, make_db: Any, **kw: Any) -> Any:
        seen.update(kw)
        return SimpleNamespace(notes=[], items={}, probes={})

    monkeypatch.setattr(probes, "run_probes", fake)
    report = SimpleNamespace(findings=[], notes=[], rules=None)
    chk._layer_r(
        SimpleNamespace(), report, lambda _db: None, lambda v, p: p, None,
        login=None, mint_token=None, sessions=lambda db: None, scheme="Bearer", only=None,
        runtime=runtime, options=options,
    )  # fmt: skip
    return seen


def test_a_dotnet_runtime_relogs_each_probe(monkeypatch):
    seen = _run(monkeypatch, AppRuntime(framework="dotnet"))
    assert seen["relogin"] is True and seen["crash_admits"] is True


def test_the_caller_may_still_choose(monkeypatch):
    seen = _run(monkeypatch, AppRuntime(framework="dotnet"), {"relogin": False, "crash_admits": False})
    assert seen["relogin"] is False and seen["crash_admits"] is False


@pytest.mark.parametrize("framework", ["go", "fastapi", "flask", "django"])
def test_other_runtimes_are_called_as_before(monkeypatch, framework):
    for runtime in (AppRuntime(framework=framework), None):
        seen = _run(monkeypatch, runtime)
        assert "relogin" not in seen and "crash_admits" not in seen
