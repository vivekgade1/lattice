"""RC3: does the project have an EF Core migration model snapshot (``*ModelSnapshot.cs``), and does it state
snake_case column names? (docs/results/2026-10-18-dotnet-prereg.md, 5.1 and N6; expected for D1: yes if A1 used
migrations, Q3.)

    python dogfood/dotnet_recon/rc3_snapshot.py <project-dir> [--out FILE]

Reads only; the snapshot is C# source, read by statement: ``modelBuilder.Entity("Ns.Type", b => { ... })`` blocks,
``b.Property<T>("Name")...HasColumnName("x")`` chains and ``b.ToTable("t")``. A property with no ``HasColumnName``
keeps its property name as the column name (EF's convention). No snapshot is a normal answer (``present: false``),
not an error (the fixture ``dnparity1`` has none).
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
ENTITY = re.compile(r'modelBuilder\.Entity\(\s*"([^"]+)"\s*,\s*b\s*=>', re.S)
PROP = re.compile(r'b\.Property<([^>]+(?:<[^>]*>)?)>\(\s*"([^"]+)"\s*\)(.*?);', re.S)
COLNAME = re.compile(r'\.HasColumnName\(\s*"([^"]+)"\s*\)')
TOTABLE = re.compile(r'b\.ToTable\(\s*"([^"]+)"')


def parse(text: str) -> list[dict]:
    """Entities of one snapshot: name, table, columns [{property, type, column}]."""
    marks = [(m.start(), m.group(1)) for m in ENTITY.finditer(text)]
    out = []
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        block = text[start:end]
        table = TOTABLE.search(block)
        cols = []
        for pm in PROP.finditer(block):
            chain = pm.group(3)
            cn = COLNAME.search(chain)
            cols.append(
                {
                    "property": pm.group(2),
                    "type": pm.group(1),
                    "column": cn.group(1) if cn else pm.group(2),
                    "has_column_name": bool(cn),
                }
            )
        out.append(
            {
                "entity": name,
                "table": table.group(1) if table else name.rsplit(".", 1)[-1],
                "columns": cols,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("root")
    ap.add_argument("--out")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    snaps = [p for p in C.source_files(root, ".cs") if p.name.endswith("ModelSnapshot.cs")]
    result: dict = {
        "rc": "RC3",
        "root": root.name,
        "present": bool(snaps),
        "snapshots": [p.relative_to(root).as_posix() for p in snaps],
    }
    if snaps:
        entities = [e for p in snaps for e in parse(p.read_text(encoding="utf-8-sig"))]
        cols = [(e["table"], c["column"]) for e in entities for c in e["columns"]]
        snake = [t for t in cols if SNAKE.match(t[1])]
        result.update(
            {
                "tables": sorted({e["table"] for e in entities}),
                "tables_snake_case": all(SNAKE.match(e["table"]) for e in entities),
                "columns": len(cols),
                "columns_snake_case": len(snake),
                "share_snake_case": round(len(snake) / len(cols), 3) if cols else None,
                "non_snake_case": sorted(f"{t}.{c}" for t, c in cols if not SNAKE.match(c)),
                "verdict": "snake_case" if cols and len(snake) == len(cols) else "not snake_case",
            }
        )
    else:
        result["verdict"] = (
            "no snapshot (names come from ToTable/HasColumnName/conventions, N6 step 2)"
        )
    C.emit(result, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
