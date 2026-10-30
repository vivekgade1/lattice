"""scripts/audit_transcript_generic.py: the two false positives of the H2 round (a `//` comment marker in a heredoc; `../` hops over `$d`/`$n`
loop variables) no longer void, and real escapes still do. Hand-built transcripts plus both real H2 transcripts (the frozen auditor stays the
reference for what was flagged then)."""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
H2 = REPO / "docs/results/data/2026-10-23-dotnet-h2"
HOME = os.path.expanduser("~")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


G = load(REPO / "scripts/audit_transcript_generic.py", "audit_transcript_generic")
ROOT = f"{HOME}/Documents/dn-gen-test"


def transcript(tmp_path: Path, calls: list[tuple[str, dict]], cwd: str = ROOT) -> Path:
    f = tmp_path / "t.jsonl"
    f.write_text(
        "\n".join(
            json.dumps(
                {
                    "type": "assistant",
                    "cwd": cwd,
                    "sessionId": "s",
                    "message": {"content": [{"type": "tool_use", "name": n, "input": i}]},
                }
            )
            for n, i in calls
        )
        + "\n"
    )
    return f


def run(tmp_path: Path, *cmds: str, root: str = ROOT, **kw):
    return G.audit(transcript(tmp_path, [("Bash", {"command": c}) for c in cmds]), root, **kw)


# ---- the two false positives ---------------------------------------------------------------------------------------------------------------------


CS_HEREDOC = """mkdir -p scratch tests/spec/apiary/{Data,Domain} && cd tests/spec/apiary &&
cat > Domain/Entities.cs <<'EOF'
namespace Apiary.Domain;
// a line comment, not a path
public class Hive { public int Id { get; set; } } // trailing //
var s = $"{what} not found"; // $must be {min} to {max} characters
builder.WebHost.UseUrls($"http://127.0.0.1:{port}");
EOF
cat > Data/Db.cs <<'EOF'
// another one
EOF
python3 - <<'EOF'
p='Endpoints/A.cs'
s=open(p).read().replace('x // y', 'z')
open(p,'w').write(s)
EOF
cd ../../..
"""

W3_LOOP = """cd {root}
export PATH=/opt/homebrew/bin:$PATH DOTNET_ROOT=/opt/homebrew/opt/dotnet/libexec DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
export HOME=$PWD/scratch/home DOTNET_CLI_HOME=$PWD/scratch/home NUGET_PACKAGES=$PWD/scratch/home/.nuget/packages TMPDIR=$PWD/scratch/.tmp
rm -rf scratch/build; mkdir -p scratch/build
for n in 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16; do
 d=scratch/build/m$n; mkdir -p $d; cp -R tests/spec/apiary/. $d/
 (cd $d && patch -p1 -s < ../../../mutants/m$n.diff) || echo PATCHFAIL $n
 echo "m$n: $(dotnet build $d/apiary.csproj -c Release -p:RestoreLockedMode=true 2>&1 | grep -E 'Error\\(s\\)|error ' | head -3)"
done
"""


def test_csharp_comment_marker_in_heredocs_is_not_a_path(tmp_path: Path) -> None:
    a = run(tmp_path, CS_HEREDOC)
    assert a["verdict"] == "pass"
    assert not a["outside"] and not a["forbidden"]
    assert not any(x["token"] == "//" for x in a["review"])


def test_loop_variables_and_dotdot_hops_resolve_inside_the_root(tmp_path: Path) -> None:
    a = run(tmp_path, W3_LOOP.format(root=ROOT))
    assert a["verdict"] == "pass"
    assert not a["outside"] and not a["forbidden"]
    assert not any(
        "mutants" in str(x["token"]) for x in a["review"]
    )  # resolved, not merely demoted


def test_loop_hops_that_leave_the_root_still_void(tmp_path: Path) -> None:
    cmd = (
        "for n in 01 02; do d=scratch/build/m$n; (cd $d && cat ../../../../outside/m$n.diff); done"
    )
    a = run(tmp_path, cmd)
    assert a["verdict"] == "void"
    assert any("outside/m" in str(x["path"]) for x in a["outside"])


def test_unresolvable_loop_variable_is_review_not_void(tmp_path: Path) -> None:
    b = run(tmp_path, "for f in $(ls); do cat scratch/$f/../x.txt; cat $f/data.txt; done")
    assert b["verdict"] == "pass"
    assert [x for x in b["review"] if "unresolved" in x["why"]]
    # a ../ hop that leaves the root is an escape whatever the variable is
    assert run(tmp_path, "for f in $(ls); do cat ../keep/$f; done")["verdict"] == "void"


def test_comments_and_slash_only_tokens_are_not_paths(tmp_path: Path) -> None:
    a = run(
        tmp_path,
        "ls # ../../../etc/passwd\ntrue // y //\ngrep -rn '//' src || true\nx=1 # ~/Documents/lattice",
    )
    assert a["verdict"] == "pass" and not a["review"]


def test_cd_in_a_subshell_does_not_persist(tmp_path: Path) -> None:
    assert run(tmp_path, "cd sub && cat ../notes.txt")["verdict"] == "pass"
    assert run(tmp_path, "(cd sub) && cat ../notes.txt")["verdict"] == "void"


def test_cwd_persists_across_calls(tmp_path: Path) -> None:
    assert run(tmp_path, "cd sub/dir", "cat ../../notes.txt")["verdict"] == "pass"
    assert run(tmp_path, "cd sub", "cat ../../notes.txt")["verdict"] == "void"


def test_variables_do_not_persist_across_calls(tmp_path: Path) -> None:
    a = run(tmp_path, "D=scratch", "cat $D/x")
    assert a["verdict"] == "pass" and a["review"]


# ---- the real H2 transcripts ----------------------------------------------------------------------------------------------------------------------


REAL = [
    (
        "author/a3-transcript.jsonl",
        "author/a3-audit-dispatch.json",
        f"{HOME}/Documents/dn-h2-author",
    ),
    ("w3/w3-transcript.jsonl", "w3/w3-audit.json", f"{HOME}/Documents/dn-h2-w"),
]


@pytest.mark.parametrize("tr,old_audit,root", REAL)
def test_real_h2_transcripts_pass_and_the_old_outside_items_are_gone(
    tr: str, old_audit: str, root: str
) -> None:
    if not (H2 / tr).is_file():
        pytest.skip("H2 data not in this checkout")
    old = json.loads((H2 / old_audit).read_text())
    assert (
        old["verdict"] == "void" and old["outside"]
    )  # the premise: the frozen auditor voided these honest runs
    new = G.audit(H2 / tr, root)
    assert new["verdict"] == "pass" and new["tool_calls"] == old["tool_calls"]
    flagged_before = {x["token"] for x in old["outside"]}
    assert not flagged_before & {x["token"] for x in new["outside"]}
    assert not flagged_before & {
        x["token"] for x in new["review"]
    }  # absent altogether, or at most demoted to review
    assert new["transcript_sha256"] == old["transcript_sha256"]


def test_the_frozen_h2_auditor_is_untouched() -> None:
    frozen = H2 / "audit_transcript.py"
    listed = {
        line.split("  ", 1)[1]: line.split("  ", 1)[0]
        for line in (H2 / "freeze.sha256").read_text().splitlines()
        if "  " in line
    }
    assert (
        hashlib.sha256(frozen.read_bytes()).hexdigest()
        == listed["docs/results/data/2026-10-23-dotnet-h2/audit_transcript.py"]
    )


@pytest.mark.parametrize("tr,old_audit,root", REAL)
def test_the_frozen_auditor_run_live_still_voids_the_real_transcripts(
    tr: str, old_audit: str, root: str
) -> None:
    """The reproduction of the defect, for the record: the frozen auditor, run now on the same transcripts, voids; the generic one does not."""
    if not (H2 / tr).is_file():
        pytest.skip("H2 data not in this checkout")
    old = load(H2 / "audit_transcript.py", "audit_transcript_h2_frozen")
    assert old.audit(H2 / tr, root)["verdict"] == "void"
    assert G.audit(H2 / tr, root)["verdict"] == "pass"


def test_the_old_auditor_voids_the_hand_built_csharp_heredoc(tmp_path: Path) -> None:
    old = load(H2 / "audit_transcript.py", "audit_transcript_h2_frozen")
    t = transcript(tmp_path, [("Bash", {"command": CS_HEREDOC})])
    assert old.audit(t, ROOT)["verdict"] == "void"
    assert G.audit(t, ROOT)["verdict"] == "pass"


# ---- real escapes still void ----------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd,key",
    [
        (f"ls {HOME}/Desktop", "outside"),
        ("cat ~/Desktop/notes.txt", "outside"),
        ("cd ~ && ls", "outside"),
        ("cd", "outside"),
        ("cd ~/Downloads", "outside"),
        ("cat ../secret.txt", "outside"),
        ("cd .. && ls", "outside"),
        ("cat scratch/../../x", "outside"),
        ("ls $HOME/Desktop", "outside"),
        (f"cat {HOME}/Documents/lattice/lattice/importer/rules.py", "forbidden"),
        ("cat ~/Documents/lattice/docs/HANDOFF.md", "forbidden"),
        ("ls ~/Documents/lattice-dn/lattice", "forbidden"),
        ("cat ../lattice/docs/HANDOFF.md", "forbidden"),
        ("cat ~/.claude/CLAUDE.md", "forbidden"),
        ("cat $HOME/.claude/settings.json", "forbidden"),
        ("cat ~/Documents/streak/spec.py", "forbidden"),
        ("cat ~/Documents/dn-h1-author/BRIEF.md", "forbidden"),
        ("D=$HOME/Documents/lattice; cat $D/x.py", "forbidden"),
        ("(cd ~/Documents/lattice && git log)", "forbidden"),
        ("echo $(cat ~/.claude/CLAUDE.md)", "forbidden"),
        ("bash -c 'cat ~/.claude/CLAUDE.md'", "forbidden"),
        ("cp x ~/Documents/lattice/y", "forbidden"),
        ("cat x > ~/Desktop/leak.txt", "outside"),
        ("for d in .. ../..; do ls $d; done", "outside"),
        (f"python3 - <<'EOF'\nprint(open('{HOME}/Desktop/x').read())\nEOF", "outside"),
        ("python3 -c \"import os; os.listdir('..')\"", "outside"),
        ("bash <<'EOF'\ncat ~/.claude/CLAUDE.md\nEOF", "forbidden"),
        ("cat //Users/nobody/x", "outside"),
    ],
)
def test_real_escapes_void(tmp_path: Path, cmd: str, key: str) -> None:
    a = run(tmp_path, cmd)
    assert a["verdict"] == "void", a
    assert a[key], a


def test_escapes_by_file_tools_and_other_tools_void(tmp_path: Path) -> None:
    for calls, key in [
        (
            [("Read", {"file_path": f"{HOME}/Documents/lattice/lattice/importer/rules.py"})],
            "forbidden",
        ),
        ([("Read", {"file_path": "../x.txt"})], "outside"),
        ([("Write", {"file_path": f"{HOME}/Desktop/x"})], "outside"),
        ([("Grep", {"pattern": "Owner", "path": f"{HOME}/Documents/dn-h1-author"})], "forbidden"),
        ([("Glob", {"pattern": "../*/x.py"})], "outside"),
        ([("WebFetch", {"url": "https://example.com"})], "outside"),
    ]:
        a = G.audit(transcript(tmp_path, calls), ROOT)
        assert a["verdict"] == "void" and a[key], (calls, a)
    assert (
        G.audit(
            transcript(
                tmp_path,
                [
                    ("Read", {"file_path": f"{ROOT}/BRIEF.md"}),
                    ("Write", {"file_path": "a/b.cs"}),
                    ("Glob", {"pattern": "**/*.cs"}),
                    ("TodoWrite", {}),
                ],
            ),
            ROOT,
        )["verdict"]
        == "pass"
    )


def test_transcript_cwd_outside_voids(tmp_path: Path) -> None:
    a = G.audit(
        transcript(
            tmp_path, [("Read", {"file_path": f"{ROOT}/x"})], cwd=f"{HOME}/Documents/elsewhere"
        ),
        ROOT,
    )
    assert a["verdict"] == "void"


def test_forbidden_text_in_a_data_heredoc_is_review_only(tmp_path: Path) -> None:
    a = run(tmp_path, "cat > NOTES.md <<'EOF'\nsee ~/Documents/lattice/docs/x.md\nEOF")
    assert a["verdict"] == "pass" and a["review"] and "here-document" in a["review"][0]["why"]


def test_allowlist_tmp_and_scratch(tmp_path: Path) -> None:
    assert (
        run(tmp_path, "ls /opt/homebrew/bin/dotnet /usr/bin/env /dev/null >/dev/null 2>&1")[
            "verdict"
        ]
        == "pass"
    )
    a = run(tmp_path, "echo hi > /tmp/x.log")
    assert a["verdict"] == "pass" and a["review"]
    other = f"{HOME}/Documents/dn-gen-test-scratch"
    assert run(tmp_path, f"ls {other}/out")["verdict"] == "void"
    assert run(tmp_path, f"ls {other}/out", scratch=other)["verdict"] == "pass"
    assert run(tmp_path, "cat ~/Documents/anything", forbid=(f"{HOME}/Documents/any",))["forbidden"]


# ---- the command line -----------------------------------------------------------------------------------------------------------------------------


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO / "scripts/audit_transcript_generic.py"), *args],
        capture_output=True,
        text=True,
    )


def test_cli_exit_codes_and_out_file(tmp_path: Path) -> None:
    ok = transcript(tmp_path, [("Bash", {"command": "ls"})])
    out = tmp_path / "audit.json"
    r = cli(str(ok), "--root", ROOT, "--out", str(out))
    assert r.returncode == 0 and '"verdict": "pass"' in r.stdout
    assert json.loads(out.read_text())["verdict"] == "pass"
    bad = tmp_path / "bad"
    bad.mkdir()
    r = cli(str(transcript(bad, [("Bash", {"command": "cat ~/.claude/x"})])), "--root", ROOT)
    assert r.returncode == 1 and "forbidden" in r.stdout
    assert cli(str(tmp_path / "missing.jsonl"), "--root", ROOT).returncode == 2
    assert cli(str(ok)).returncode == 2  # argparse: --root is required


def test_cli_help_documents_out_and_scratch() -> None:
    r = cli("--help")
    assert r.returncode == 0
    for needle in ("--out", "--scratch", "--self-check", "--root", "void"):
        assert needle in r.stdout


def test_self_check_passes() -> None:
    r = cli("--self-check")
    assert r.returncode == 0 and "SELF-CHECK OK" in r.stdout
