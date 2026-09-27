"""The transactional outbox behind `for_each(t, native.x, args...)` (spec 1b §3.4).

`for_each` writes one row per input row into the table `lattice_outbox`, in the same
transaction as the endpoint's writes (the runtime and the generated app create the
table when it is missing, inside that transaction). A request that rolls back -- a
declared error raised later, a UNIQUE conflict, a 500 -- leaves no outbox rows and sends
nothing. After COMMIT the request dispatches its rows in order (by id): each effect
native is called with the row's arguments and the row is marked `done`, or `failed`
with the error; the HTTP response never depends on the dispatch.

    lattice_outbox
      id          INTEGER PRIMARY KEY   -- insertion order = dispatch order
      endpoint    TEXT NOT NULL         -- the endpoint that wrote the row
      native      TEXT NOT NULL         -- the effect native to call
      args        TEXT NOT NULL         -- JSON {"types": [scalar, ...], "values": [...]}
      status      TEXT NOT NULL         -- pending | done | failed
      error       TEXT                  -- the last failure ("<Type>: <message>")
      created_at  TEXT NOT NULL         -- the request's instant (fn.now())
      attempts    INTEGER NOT NULL      -- dispatches so far

`args` holds the stored values (as the database keeps them) with their Lattice scalar
types, so any process can decode them the way the runtime hands values to natives
(Bool `bool`, Date `datetime.date`, DateTime an aware UTC `datetime.datetime`, Uuid
`uuid.UUID`; a null stays None).

Failed rows are re-dispatched by `retry(db_path, natives)` or from the command line:

    python -m lattice.api.outbox retry --db app.db --natives my_natives

(`--natives` names a module whose functions are the natives, by name; `--pending` also
re-sends rows left `pending` by a process that stopped between COMMIT and dispatch).
Delivery is at-least-once: an effect native must be idempotent.

Only the SQLite backend is implemented, so the statements here are SQLite's.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lattice.api.backend import SQLITE
from lattice.typesys import Scalar

__all__ = [
    "CREATE_SQL",
    "OUTBOX_TABLE",
    "RetryReport",
    "decode_args",
    "dispatch",
    "main",
    "retry",
]

OUTBOX_TABLE = "lattice_outbox"
CREATE_SQL = (
    f'CREATE TABLE IF NOT EXISTS "{OUTBOX_TABLE}" ('
    '"id" INTEGER PRIMARY KEY, "endpoint" TEXT NOT NULL, "native" TEXT NOT NULL, '
    '"args" TEXT NOT NULL, "status" TEXT NOT NULL, "error" TEXT, '
    '"created_at" TEXT NOT NULL, "attempts" INTEGER NOT NULL)'
)
MARK_DONE_SQL = (
    f'UPDATE "{OUTBOX_TABLE}" SET "status" = \'done\', "error" = NULL, '
    '"attempts" = "attempts" + 1 WHERE "id" = ?'
)
MARK_FAILED_SQL = (
    f'UPDATE "{OUTBOX_TABLE}" SET "status" = \'failed\', "error" = ?, '
    '"attempts" = "attempts" + 1 WHERE "id" = ?'
)
EXISTS_SQL = f"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = '{OUTBOX_TABLE}'"
RETRY_SQL = (
    f'SELECT "id", "native", "args" FROM "{OUTBOX_TABLE}" '
    "WHERE \"status\" = 'failed' ORDER BY \"id\""
)
RETRY_PENDING_SQL = (
    f'SELECT "id", "native", "args" FROM "{OUTBOX_TABLE}" '
    "WHERE \"status\" IN ('failed', 'pending') ORDER BY \"id\""
)
ERROR_LIMIT = 500  # characters of an error message kept in the row

# (id, native, args JSON): a row as the outbox INSERT returns it and retry selects it
OutboxRow = Sequence[Any]
Natives = Mapping[str, Callable[..., object]]


def decode_args(args: str) -> list[object]:
    """The Python arguments of an outbox row's `args` JSON."""
    data = json.loads(args)
    return [
        None if v is None else SQLITE.native_arg(v, Scalar(t))
        for t, v in zip(data["types"], data["values"], strict=True)
    ]


def _error_text(err: BaseException) -> str:
    return f"{type(err).__name__}: {err}"[:ERROR_LIMIT]


def dispatch(conn: Any, rows: Sequence[OutboxRow], natives: Natives) -> tuple[int, int]:
    """Call each row's native, in id order, and mark the row done or failed (each mark
    commits on its own: `conn` is in autocommit mode). Never raises for a failing
    native or a failing mark. Returns (done, failed)."""
    done = failed = 0
    for row_id, native, args in sorted(rows, key=lambda r: r[0]):
        fn = natives.get(native)
        error: str | None = None
        if fn is None:
            error = f"no implementation for native {native!r}"
        else:
            try:
                fn(*decode_args(args))
            except Exception as err:  # the effect failed: recorded, retried later
                error = _error_text(err)
        try:
            if error is None:
                conn.execute(MARK_DONE_SQL, (row_id,))
            else:
                conn.execute(MARK_FAILED_SQL, (error, row_id))
        except sqlite3.Error:
            pass  # the row keeps its status; retry sends it again (at-least-once)
        if error is None:
            done += 1
        else:
            failed += 1
    return done, failed


@dataclass(frozen=True)
class RetryReport:
    done: int
    failed: int


def retry(db_path: str | Path, natives: Natives, *, pending: bool = False) -> RetryReport:
    """Re-dispatch the failed rows of the outbox at `db_path` (with `pending`, also the
    rows never dispatched), in id order. A database without an outbox has none."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        if conn.execute(EXISTS_SQL).fetchone() is None:
            return RetryReport(0, 0)
        rows = conn.execute(RETRY_PENDING_SQL if pending else RETRY_SQL).fetchall()
        done, failed = dispatch(conn, rows, natives)
        return RetryReport(done, failed)
    finally:
        conn.close()


def _module_natives(name: str) -> dict[str, Callable[..., object]]:
    module = importlib.import_module(name)
    return {
        attr: value
        for attr, value in vars(module).items()
        if callable(value) and not attr.startswith("_")
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lattice.api.outbox", description="Lattice outbox (for_each effects)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    again = commands.add_parser("retry", help="re-dispatch the failed outbox rows")
    again.add_argument("--db", required=True, help="the SQLite database file")
    again.add_argument(
        "--natives", required=True, help="the module holding the effect natives (by name)"
    )
    again.add_argument(
        "--pending", action="store_true", help="also send rows left pending (never dispatched)"
    )
    args = parser.parse_args(argv)
    if not Path(args.db).is_file():
        parser.error(f"no database file {args.db!r}")
    report = retry(args.db, _module_natives(args.natives), pending=args.pending)
    print(
        f"retried {report.done + report.failed} row(s): "
        f"{report.done} done, {report.failed} failed"
    )
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
