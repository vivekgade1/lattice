"""The spec tools' error on a directory without a spec (lattice/mcp/tools.py,
``PendingBackend._resolve``; API.md v1.20, F5 of docs/results/2026-10-14-agent-run-3-prereg.md):
the text is unchanged for every directory; a contract consumer's (one holding
``lattice.imports.toml`` and/or ``lattice.contracts.lock.json``) gets one more sentence."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.mcp.tools import PendingBackend, Toolbox, ToolError

IMPORTS, LOCK = "lattice.imports.toml", "lattice.contracts.lock.json"


def sentence(files: str) -> str:
    return (
        f" This directory is a contract consumer (it holds {files}), which needs no spec for its "
        "contracts: with a workspace, contract_check and linked_question_list show what its "
        "providers changed and the notes they left (otherwise `lattice contracts check` and "
        "`lattice contracts questions list` with --workspace)."
    )


def error_of(root: Path) -> str:
    with pytest.raises(ToolError) as e:
        Toolbox(PendingBackend(root)).call("model_get", {})
    return str(e.value)


def test_a_plain_directory_gets_the_old_text(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    text = error_of(root)
    assert text == (
        f"{root.resolve()} is not a Lattice project yet (missing: REQUIREMENTS.md, spec.py, "
        "spec.trace.json). Write REQUIREMENTS.md (the rules in plain language), then run "
        "`lattice spec draft` in this directory to draft spec.py and spec.trace.json; "
        "after that these tools work without restarting."
    )


@pytest.mark.parametrize(
    ("present", "files"),
    [
        ((IMPORTS,), IMPORTS),
        ((LOCK,), LOCK),
        ((IMPORTS, LOCK), f"{IMPORTS} and {LOCK}"),
    ],
)
def test_a_consumer_directory_gets_one_sentence_after_the_old_text(
    tmp_path: Path, present: tuple[str, ...], files: str
) -> None:
    root = tmp_path / "cons"
    root.mkdir()
    old = error_of(root)
    for n in present:
        (root / n).write_text("")
    text = error_of(root)
    assert text.startswith(old) and text == old + sentence(files)
    assert text.count("contract_check") == 1


def test_other_files_add_nothing(tmp_path: Path) -> None:
    root = tmp_path / "other"
    root.mkdir()
    old = error_of(root)
    (root / "studio.toml").write_text("")
    (root / "lattice.workspace.toml").write_text("")
    assert error_of(root) == old
