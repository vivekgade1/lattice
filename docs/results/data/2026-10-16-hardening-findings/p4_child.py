"""P4 child: describe()/install() summaries for fixed fixtures, under the tree on sys.path[0].

usage: python p4_child.py TREE WORKTREE FIXROOT   (prints one JSON object)
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

tree, worktree, fixroot = (Path(a).resolve() for a in sys.argv[1:4])
sys.path.insert(0, str(tree))
import lattice.modules as mods  # noqa: E402

assert Path(mods.__file__).resolve().is_relative_to(tree), mods.__file__

spec = importlib.util.spec_from_file_location("gitfix", worktree / "tests/modules/gitfix.py")
gf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gf)


def mk(case: str, files: dict[str, str]) -> Path:
    d = fixroot / case
    if d.exists():
        shutil.rmtree(d)
    gf.write_files(d, files)
    return d


adapter_files = gf.module_files()
adapter_files["engine.toml"] = adapter_files["engine.toml"].replace(
    'adapter = "mysql"', 'adapter = "evil:Adapter"'
)
cases: dict[str, Path] = {
    "a": mk("a", gf.module_files()),
    "b": mk("b", adapter_files),
    "c": mk(
        "c",
        {"lattice-module.toml": 'name = "audit"\nversion = "1"\nkind = "custom"\n'
         'provides = ["audit"]\n'},
    ),
    "d": mk(
        "d",
        {"lattice-module.toml": 'name = "plain"\nversion = "1"\nkind = "custom"\n'
         'description = "a described module"\n'},
    ),
    "e": mk("e", gf.module_files(name="mariadb-e")),
    "g": mk(
        "g",
        {"lattice-module.toml": 'name = "custom-mod"\nversion = "1"\nkind = "custom"\n'
         'entry = "evilentry"\n', "evilentry.py": "pass\n"},
    ),
    "h": mk(
        "h",
        {"lattice-module.toml": 'name = "weird"\nversion = "1"\nkind = "custom"\n'
         'entry = "a\\nb"\n'},
    ),
    "i": mk(
        "i",
        {"lattice-module.toml": 'name = "emptyentry"\nversion = "1"\nkind = "custom"\n'
         'entry = ""\n'},
    ),
}
cases["f"] = tree / "lattice/security/module"

out: dict[str, object] = {}
for case in sorted(cases):
    d = cases[case]
    m = mods.read_manifest(d)
    sha = mods.tree_hash(d)
    kw = {}
    if case == "e":
        kw = {"commit": "0123456789abcdef (pinned fixture commit)", "dropped": ("x.txt", "y.txt")}
    src = f"<fixed>/{case}"
    out[case] = {
        "describe": mods.describe(m, sha, src, **kw),
        "entry": m.entry,
    }

# install(...) with the confirmation refused: the printed summary and the refusal
for case in ("a", "c"):
    proj = fixroot.parent / f"proj-{case}"
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)
    lines: list[str] = []
    questions: list[str] = []

    def no(q: str, _qs: list[str] = questions) -> bool:
        _qs.append(q)
        return False

    try:
        mods.install(proj, str(cases[case]), confirm=no, out=lines.append)
        refused = None
    except mods.Refused as err:
        refused = str(err)
    out[f"install-{case}"] = {
        "out": "\n".join(lines).replace(str(cases[case]), "<src>").replace(str(proj), "<proj>"),
        "questions": [q.replace(str(proj), "<proj>") for q in questions],
        "refused": refused,
    }
print(json.dumps(out, indent=1, sort_keys=True))
