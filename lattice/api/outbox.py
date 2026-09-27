"""The transactional outbox behind `for_each(t, native.x, args...)` (spec 1b §3.4).

`for_each` writes one row per input row into the table `lattice_outbox`, in the same
transaction as the endpoint's writes (the runtime and the generated app create the
table when it is missing, inside that transaction). A request that rolls back -- a
declared error raised later, a UNIQUE conflict, a 500 -- leaves no outbox rows and sends
nothing. After COMMIT the request dispatches its rows in order (by id). Each row is
first *claimed* -- one UPDATE moves it to `dispatching`, counts the attempt and stamps
`claimed_at`, and only a dispatcher whose claim changed the row sends it, so two
dispatchers racing on a row send it once. Then its effect native is called with the
row's arguments and the row is marked `done`, or `failed` with the error. A mark that
fails is tried again briefly and then logged (logger `lattice.outbox`); the row stays
`dispatching` and a retry re-sends it once its claim is older than the grace period.
The HTTP response never depends on the dispatch.

    lattice_outbox
      id          INTEGER PRIMARY KEY   -- insertion order = dispatch order
      endpoint    TEXT NOT NULL         -- the endpoint that wrote the row
      native      TEXT NOT NULL         -- the effect native to call
      args        TEXT NOT NULL         -- JSON {"types": [scalar, ...], "values": [...]}
      status      TEXT NOT NULL         -- pending | dispatching | done | failed
      error       TEXT                  -- the last failure ("<Type>: <message>")
      created_at  TEXT NOT NULL         -- the request's instant (fn.now())
      attempts    INTEGER NOT NULL      -- claims (dispatches) so far
      claimed_at  TEXT                  -- the last claim's instant (UTC, ISO with Z)

`pending` is a row written but not yet claimed, `dispatching` a row claimed and being
sent (or whose mark was lost). A table created before `claimed_at` existed gains the
column on its first dispatch or retry.

`args` holds the stored values (as the database keeps them) with their Lattice scalar
types, so any process can decode them the way the runtime hands values to natives
(Bool `bool`, Date `datetime.date`, DateTime an aware UTC `datetime.datetime`, Uuid
`uuid.UUID`; a null stays None).

Rows are re-dispatched by `retry(db_path, natives)` or from the command line:

    python -m lattice.api.outbox retry --db app.db --natives my_natives [--grace 300]

It takes every `failed` row, and the `pending` or `dispatching` rows whose claim (or,
never claimed, whose creation) is older than the grace period (`GRACE_SECONDS`, or
`--grace`): a process that stopped between COMMIT and dispatch, or a lost mark. A
younger row may still be in flight and is left alone. `--natives` names a module whose
functions are the natives, by name; `--pending` also takes every `pending` row whatever
its age (the claim still keeps an in-flight one from being sent twice). Delivery is
at-least-once: an effect native must be idempotent.

Only the SQLite backend is implemented, so the statements here are SQLite's.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import logging
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lattice.api.backend import SQLITE, datetime_text
from lattice.typesys import Scalar

__all__ = [
    "CREATE_SQL",
    "GRACE_SECONDS",
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
    '"created_at" TEXT NOT NULL, "attempts" INTEGER NOT NULL, "claimed_at" TEXT)'
)
# a table created before claimed_at existed gains it (see ensure_claimed_at)
COLUMNS_SQL = f'SELECT "name" FROM pragma_table_info(\'{OUTBOX_TABLE}\')'
ADD_CLAIMED_SQL = f'ALTER TABLE "{OUTBOX_TABLE}" ADD COLUMN "claimed_at" TEXT'
# an instant (bound as the last parameter) is past a row's claim, or its creation
_STALE = 'julianday(COALESCE("claimed_at", "created_at")) <= julianday(?)'
# (claimed_at, id, cutoff): take the row unless another dispatcher holds a live claim
CLAIM_SQL = (
    f'UPDATE "{OUTBOX_TABLE}" SET "status" = \'dispatching\', '
    '"attempts" = "attempts" + 1, "claimed_at" = ? WHERE "id" = ? AND '
    "(\"status\" IN ('pending', 'failed') OR "
    f"(\"status\" = 'dispatching' AND {_STALE}))"
)
# (id, claimed_at) / (error, id, claimed_at): only the claim's holder marks the row
MARK_DONE_SQL = (
    f'UPDATE "{OUTBOX_TABLE}" SET "status" = \'done\', "error" = NULL '
    'WHERE "id" = ? AND "status" = \'dispatching\' AND "claimed_at" = ?'
)
MARK_FAILED_SQL = (
    f'UPDATE "{OUTBOX_TABLE}" SET "status" = \'failed\', "error" = ? '
    'WHERE "id" = ? AND "status" = \'dispatching\' AND "claimed_at" = ?'
)
EXISTS_SQL = f"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = '{OUTBOX_TABLE}'"
# (cutoff): failed rows, and pending / dispatching rows older than the grace period
RETRY_SQL = (
    f'SELECT "id", "native", "args" FROM "{OUTBOX_TABLE}" '
    f"WHERE \"status\" = 'failed' OR "
    f"(\"status\" IN ('pending', 'dispatching') AND {_STALE}) ORDER BY \"id\""
)
# (cutoff): the same, plus every pending row whatever its age
RETRY_PENDING_SQL = (
    f'SELECT "id", "native", "args" FROM "{OUTBOX_TABLE}" '
    f"WHERE \"status\" IN ('failed', 'pending') OR "
    f"(\"status\" = 'dispatching' AND {_STALE}) ORDER BY \"id\""
)
GRACE_SECONDS = 300  # a claim (or an unclaimed row) younger than this may be in flight
MARK_TRIES = 3  # a failing done / failed mark is tried this often, then logged
MARK_BACKOFF = 0.05  # seconds before the 2nd try (doubling)
ERROR_LIMIT = 500  # characters of an error message kept in the row

# (id, native, args JSON): a row as the outbox INSERT returns it and retry selects it
OutboxRow = Sequence[Any]
Natives = Mapping[str, Callable[..., object]]
log = logging.getLogger("lattice.outbox")


def decode_args(args: str) -> list[object]:
    """The Python arguments of an outbox row's `args` JSON."""
    data = json.loads(args)
    return [
        None if v is None else SQLITE.native_arg(v, Scalar(t))
        for t, v in zip(data["types"], data["values"], strict=True)
    ]


def _error_text(err: BaseException) -> str:
    return f"{type(err).__name__}: {err}"[:ERROR_LIMIT]


def ensure_claimed_at(conn: Any) -> None:
    """Add the claimed_at column to an outbox table created without it."""
    names = {row[0] for row in conn.execute(COLUMNS_SQL).fetchall()}
    if "claimed_at" in names:
        return
    try:
        conn.execute(ADD_CLAIMED_SQL)
    except sqlite3.OperationalError as err:
        if "duplicate column" not in str(err):  # another process added it first
            raise


def _cutoff(now: dt.datetime, grace: float) -> str:
    return (now - dt.timedelta(seconds=grace)).isoformat()


def _mark(conn: Any, sql: str, params: tuple[Any, ...], row_id: object) -> None:
    """Run a done / failed mark, trying again briefly; a mark still failing is logged
    (the row stays claimed, and a retry re-sends it after the grace period)."""
    for attempt in range(MARK_TRIES):
        try:
            conn.execute(sql, params)
            return
        except sqlite3.Error as err:
            if attempt + 1 == MARK_TRIES:
                log.warning(
                    "could not mark outbox row %s (%s: %s); a retry re-sends it after "
                    "the grace period",
                    row_id,
                    type(err).__name__,
                    err,
                )
                return
            time.sleep(MARK_BACKOFF * 2**attempt)


def dispatch(
    conn: Any,
    rows: Sequence[OutboxRow],
    natives: Natives,
    *,
    now: dt.datetime | None = None,
    grace: float = GRACE_SECONDS,
) -> tuple[int, int]:
    """Claim each row, in id order, then call its native and mark it done or failed
    (each statement commits on its own: `conn` is in autocommit mode). A row another
    dispatcher holds (a claim younger than `grace` seconds, or already done) is skipped.
    Never raises for a failing native or a failing mark. Returns (done, failed) of the
    rows this call sent."""
    done = failed = 0
    if not rows:
        return done, failed
    ensure_claimed_at(conn)
    for row_id, native, args in sorted(rows, key=lambda r: r[0]):
        stamp = now if now is not None else dt.datetime.now(dt.UTC)
        claimed_at = datetime_text(stamp)
        try:
            claimed = conn.execute(CLAIM_SQL, (claimed_at, row_id, _cutoff(stamp, grace)))
        except sqlite3.Error as err:  # not claimed, not sent: a retry takes it later
            log.warning("could not claim outbox row %s (%s: %s)", row_id, type(err).__name__, err)
            continue
        if claimed.rowcount != 1:
            continue  # another dispatcher holds it, or it is done
        fn = natives.get(native)
        error: str | None = None
        if fn is None:
            error = f"no implementation for native {native!r}"
        else:
            try:
                fn(*decode_args(args))
            except Exception as err:  # the effect failed: recorded, retried later
                error = _error_text(err)
        if error is None:
            _mark(conn, MARK_DONE_SQL, (row_id, claimed_at), row_id)
            done += 1
        else:
            _mark(conn, MARK_FAILED_SQL, (error, row_id, claimed_at), row_id)
            failed += 1
    return done, failed


@dataclass(frozen=True)
class RetryReport:
    done: int
    failed: int


def retry(
    db_path: str | Path,
    natives: Natives,
    *,
    pending: bool = False,
    grace: float = GRACE_SECONDS,
    now: dt.datetime | None = None,
) -> RetryReport:
    """Re-dispatch, in id order, the failed rows of the outbox at `db_path` and its
    pending / dispatching rows claimed (or created) more than `grace` seconds before
    `now`; with `pending`, every pending row. A database without an outbox has none."""
    now = now if now is not None else dt.datetime.now(dt.UTC)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        if conn.execute(EXISTS_SQL).fetchone() is None:
            return RetryReport(0, 0)
        ensure_claimed_at(conn)
        sql = RETRY_PENDING_SQL if pending else RETRY_SQL
        rows = conn.execute(sql, (_cutoff(now, grace),)).fetchall()
        done, failed = dispatch(conn, rows, natives, now=now, grace=grace)
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
    again = commands.add_parser(
        "retry",
        help="re-dispatch the failed outbox rows and the pending / in-flight rows older "
        "than the grace period",
    )
    again.add_argument("--db", required=True, help="the SQLite database file")
    again.add_argument(
        "--natives", required=True, help="the module holding the effect natives (by name)"
    )
    again.add_argument(
        "--grace",
        type=float,
        default=GRACE_SECONDS,
        help="seconds after which a pending or claimed (in-flight) row counts as abandoned "
        f"and is re-sent (default {GRACE_SECONDS})",
    )
    again.add_argument(
        "--pending",
        action="store_true",
        help="also send every pending (never claimed) row, whatever its age",
    )
    args = parser.parse_args(argv)
    if not Path(args.db).is_file():
        parser.error(f"no database file {args.db!r}")
    if args.grace < 0:
        parser.error("--grace must be >= 0")
    report = retry(
        args.db, _module_natives(args.natives), pending=args.pending, grace=args.grace
    )
    print(
        f"retried {report.done + report.failed} row(s): "
        f"{report.done} done, {report.failed} failed"
    )
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
