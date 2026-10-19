"""L1b (gRPC round 5): a path under a protected *directory* is protected, named by the directory
entry; the exact-file behaviour is unchanged."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lattice.mcp.guard import Guard


def ev(tool: str, cwd: Path, **ti: Any) -> dict[str, Any]:
    return {"tool_name": tool, "tool_input": ti, "cwd": str(cwd), "hook_event_name": "PreToolUse"}


def test_prot_guard_denies_a_path_under_a_protected_directory(tmp_path: Path) -> None:
    p = tmp_path / "proj"
    (p / "proto" / "toy" / "v1").mkdir(parents=True)
    (p / "proto" / "toy" / "v1" / "shelf.proto").write_text("syntax = 'proto3';\n")
    (p / "protocol.md").write_text("not under the directory\n")
    (p / "spec.py").write_text("x")
    (p / "studio.toml").write_text('[mcp]\nprotected = ["proto"]\n')
    g = Guard(p)
    deep = p / "proto" / "toy" / "v1" / "shelf.proto"
    assert g.decide(ev("Edit", p, file_path=str(deep))) == "proto"
    assert g.decide(ev("Write", p, file_path="proto/toy/v1/new.proto")) == "proto"  # not yet there
    assert g.decide(ev("Bash", p, command="rm proto/toy/v1/shelf.proto")) == "proto"
    assert g.decide(ev("Bash", p, command="echo x > proto/added.proto")) == "proto"
    # unchanged: an exact protected file, and a sibling that only shares the prefix
    assert g.decide(ev("Edit", p, file_path=str(p / "spec.py"))) == "spec.py"
    assert g.decide(ev("Edit", p, file_path=str(p / "protocol.md"))) is None
    assert g.decide(ev("Edit", p, file_path=str(p / "main.go"))) is None
