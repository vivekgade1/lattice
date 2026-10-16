"""`lattice import inventory`: capture the facts the implementation diff reads, once, into a
directory outside the target (docs/results/2026-10-15-implementation-diff-prereg.md, D1).

::

    python -m lattice.cli import inventory <app_root> --out DIR [--package .] [--workflows FILE]

This is the only command of the implementation diff that writes. It writes, in ``DIR``:

- ``extract.json``: Spike A's extract of the Go module, canonical (sorted keys, ending in a
  newline);
- ``workflows.json``: a copy of ``--workflows FILE`` after validation (schema
  ``lattice.workflows/1``), when given;
- ``inventory.json``: ``schema "lattice.implementation-inventory/1"``, ``frontend``,
  ``package``, ``extract_sha256``, ``workflows_sha256`` (or null) and ``source {sha256,
  files[{path, sha256}]}``: every ``*.go``, ``go.mod`` and ``go.sum`` under the app root
  except ``_SKIP_DIRS``, ``testdata`` and dot-directories. No absolute path, time or git call.

``--out`` under the app root is refused (exit 2): nothing is ever written into the target.
Exit code: 0 captured, 2 refused or failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lattice.spec.implementation import (
    SCHEMA_INVENTORY,
    WORKFLOWS_SCHEMA,
    ImplementationError,
    canonical_json,
    sha256_bytes,
    source_record,
)

__all__ = ["capture", "main"]


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def capture(
    app_root: Path | str,
    out: Path | str,
    *,
    package: str = ".",
    workflows: Path | str | None = None,
    extract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the inventory of `app_root` into `out` and return its ``inventory.json``.
    `extract`: an extract already taken (tests); default: ``gocheck extract`` on the module.
    Raises :class:`ImplementationError` (``bad_workflows``) and ``ValueError`` (``--out``
    under the app root)."""
    root = Path(app_root).resolve()
    dest = Path(out)
    if _under(dest, root):
        raise ValueError(f"--out {dest} is under the app root {root}: nothing is written there")
    wf_bytes: bytes | None = None
    if workflows is not None:
        wf_path = Path(workflows)
        try:
            wf_bytes = wf_path.read_bytes()
            doc = json.loads(wf_bytes)
        except (OSError, ValueError) as err:
            raise ImplementationError("bad_workflows", f"cannot read {wf_path}: {err}") from None
        if not isinstance(doc, dict) or doc.get("schema") != WORKFLOWS_SCHEMA:
            raise ImplementationError(
                "bad_workflows", f"{wf_path} schema is not {WORKFLOWS_SCHEMA!r}"
            )
    if extract is None:
        from lattice.importer.frontends.go_gin import GoGinFrontEnd

        extract = GoGinFrontEnd().extract_json(root, package)
    ex_bytes = canonical_json(extract).encode()
    meta: dict[str, Any] = {
        "schema": SCHEMA_INVENTORY,
        "frontend": "go_gin",
        "package": package,
        "extract_sha256": sha256_bytes(ex_bytes),
        "workflows_sha256": sha256_bytes(wf_bytes) if wf_bytes is not None else None,
        "source": source_record(root),
    }
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "extract.json").write_bytes(ex_bytes)
    if wf_bytes is not None:
        (dest / "workflows.json").write_bytes(wf_bytes)
    elif (dest / "workflows.json").exists():
        (dest / "workflows.json").unlink()  # an earlier capture's copy must not outlive this one
    (dest / "inventory.json").write_text(canonical_json(meta), encoding="utf-8")
    return meta


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="lattice import inventory",
        description="Capture the extract, workflows and source hashes of a Go app "
        "(writes --out only).",
    )
    p.add_argument("app_root", type=Path)
    p.add_argument(
        "--out", type=Path, required=True, help="the inventory directory (not under app_root)"
    )
    p.add_argument("--package", default=".", help="the Go module directory under app_root")
    p.add_argument(
        "--workflows", type=Path, default=None, help="a workflows.json (lattice.workflows/1)"
    )
    try:
        args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        meta = capture(args.app_root, args.out, package=args.package, workflows=args.workflows)
    except ImplementationError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 -- any failure to capture is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    n = len(meta["source"]["files"])
    print(
        f"{args.out}: inventory of {n} source files, workflows {'yes' if args.workflows else 'no'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
