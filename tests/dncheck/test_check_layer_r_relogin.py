"""``lattice check`` layer R on a .NET app logs in again in each probe's session (node F5): an ASP.NET Core
app signs its tokens with a key made at start-up, so a token minted in one probe's process is refused in the
next. The Python and Go call paths are unchanged (no ``relogin`` option unless the caller passes one)."""

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
    assert _run(monkeypatch, AppRuntime(framework="dotnet"))["relogin"] is True


def test_the_caller_may_still_choose(monkeypatch):
    assert _run(monkeypatch, AppRuntime(framework="dotnet"), {"relogin": False})["relogin"] is False


@pytest.mark.parametrize("framework", ["go", "fastapi", "flask", "django"])
def test_other_runtimes_are_called_as_before(monkeypatch, framework):
    assert "relogin" not in _run(monkeypatch, AppRuntime(framework=framework))
    assert "relogin" not in _run(monkeypatch, None)
