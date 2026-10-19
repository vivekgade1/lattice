"""Round 5, S: ``goextract_grpc.go`` is split into three files of one package. Step 0 was a pure
move (the runner's P5-SPLIT proves it against the S0 tree); here, what must hold of the files at
any later point."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "lattice/gocheck/src"
FILES = ("goextract_grpc.go", "goextract_grpc_auth.go", "goextract_grpc_codes.go")
BASE = "978b468"


def top_level_names(text: str) -> set[str]:
    names = set(re.findall(r"^func (?:\([^)]*\) )?(\w+)\(", text, re.M))
    names |= set(re.findall(r"^(?:type|var|const) (\w+)", text, re.M))
    return names


def test_the_three_files_exist_and_none_exceeds_the_structural_cap() -> None:
    for f in FILES:
        assert (SRC / f).is_file()
        assert len((SRC / f).read_text().splitlines()) <= 1200


def test_every_declaration_of_the_original_file_is_in_one_of_the_three() -> None:
    proc = subprocess.run(
        ["git", "show", f"{BASE}:lattice/gocheck/src/goextract_grpc.go"],
        cwd=SRC,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip("the base commit is not in this clone")
    now: set[str] = set()
    for f in FILES:
        now |= top_level_names((SRC / f).read_text())
    assert top_level_names(proc.stdout) - now == set()


def test_each_file_is_a_non_empty_part_of_package_main() -> None:
    for f in FILES:
        text = (SRC / f).read_text()
        assert re.search(r"^package main$", text, re.M)
        assert top_level_names(text)


def test_a_declaration_lives_in_exactly_one_of_the_three_files() -> None:
    seen: dict[str, str] = {}
    for f in FILES:
        for n in top_level_names((SRC / f).read_text()):
            if not re.fullmatch(r"(?:Unmarshal|String|Len|Less|Swap)", n):
                assert n not in seen or n == "init", (n, f, seen.get(n))
            seen[n] = f


def test_the_split_and_the_new_reader_files_are_gofmt_clean() -> None:
    gofmt = shutil.which("gofmt")
    if gofmt is None:
        pytest.skip("gofmt is not installed")
    files = [str(SRC / f) for f in (*FILES, "goextract_sqlraw.go", "goextract_proto.go")]
    out = subprocess.run([gofmt, "-l", *files], capture_output=True, text=True, check=False)
    assert out.stdout.strip() == ""
