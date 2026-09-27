"""Loop corpus 12 -- T2: replay login events through a lockout state machine.

Loop shape: one pass over ordered events carrying several interacting accumulators
            per user (consecutive failures, a lock deadline, a session count, a
            status) whose next values depend on the current state and the event kind.
Construct:  a @procedure (T2): ordinary Python in the checked subset, fuel-bounded.
            No T0/T1 construct expresses it (see README.md); checked as T0, the loop
            is still rejected (L127).
Targets:    the compiled procedure (lattice.t2), and an endpoint calling it
            whole-value through the API runtime.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from lattice.profile import ApiProfile, Profile
from lattice.typesys import Scalar, Schema

SHAPE = "multi-accumulator state machine"
CONSTRUCT = "@procedure (T2)"
HINT = "native function"  # what the L127 hint on the original loop (as T0) names
LOCK_AFTER = 3  # consecutive failures
LOCK_MINUTES = 30


# --- original -----------------------------------------------------------------------------
def replay_logins(events: list[dict]) -> list[dict]:
    """Security audit: the account state after each login event (lock after 3
    consecutive failures for 30 minutes; a success clears the failures; an admin
    reset clears the lock; attempts during a lock are refused and do not count)."""
    state: dict[int, dict] = {}
    out = []
    for ev in sorted(events, key=lambda e: (e["user_id"], e["minute"], e["id"])):
        s = state.setdefault(ev["user_id"], {"failures": 0, "locked_until": None, "sessions": 0})
        if s["locked_until"] is not None and ev["minute"] >= s["locked_until"]:
            s["locked_until"], s["failures"] = None, 0
        if ev["kind"] == "reset":
            s["locked_until"], s["failures"] = None, 0
            status = "reset"
        elif s["locked_until"] is not None:
            status = "refused"
        elif ev["kind"] == "fail":
            s["failures"] += 1
            if s["failures"] >= LOCK_AFTER:
                s["locked_until"] = ev["minute"] + LOCK_MINUTES
                status = "locked"
            else:
                status = "failed"
        else:
            s["failures"] = 0
            s["sessions"] += 1
            status = "ok"
        out.append({**ev, "status": status, "sessions": s["sessions"]})
    return out


original = replay_logins


def generate(rng: random.Random) -> dict[str, list[dict]]:
    events = [
        {
            "id": i,
            "user_id": rng.randint(1, 3),
            "minute": rng.randint(0, 120),
            "kind": rng.choice(["fail", "fail", "fail", "ok", "reset"]),
        }
        for i in range(1, rng.randint(0, 40) + 1)
    ]
    return {"events": events}


# --- Lattice (T2) -------------------------------------------------------------------------
# The same state machine as a checked, fuel-instrumented @procedure over records, and an
# endpoint that hands it the (bounded) events and returns its rows as a List.

S = Scalar
LOGIN_EVENTS = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("minute", S.INT), ("kind", S.STR)],
    keys=[("id",)],
)
USERS = Schema.from_pairs(
    [("id", S.INT), ("role", S.STR), ("token", S.STR)], keys=[("id",)], private=["token"]
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"Users": USERS, "LoginEvents": LOGIN_EVENTS},
        table_names={"Users": "users", "LoginEvents": "login_events"},
        roles=frozenset({"Public", "Admin"}),
    ),
)
TOKEN = "tok-admin"
MAX_EVENTS = 100  # the endpoint's bound on the events it replays (L439)

LATTICE = """# lattice: v1
LOCK_AFTER = 3
LOCK_MINUTES = 30


@record
class LoginEvent:
    id: Int
    user_id: Int
    minute: Int
    kind: Str


@record
class LoginState:
    failures: Int
    locked_until: Int | None
    sessions: Int


@record
class LoginResult:
    id: Int
    user_id: Int
    minute: Int
    kind: Str
    status: Str
    sessions: Int


@procedure
def replay_logins(events: list[LoginEvent]) -> list[LoginResult]:
    state: dict[Int, LoginState] = {}
    out: list[LoginResult] = []
    for ev in sorted(events, key=lambda e: (e.user_id, e.minute, e.id)):
        s = state.get(ev.user_id)
        if s is None:
            s = LoginState(failures=0, locked_until=None, sessions=0)
        failures = s.failures
        locked_until = s.locked_until
        sessions = s.sessions
        if locked_until is not None and ev.minute >= locked_until:
            locked_until = None
            failures = 0
        if ev.kind == "reset":
            locked_until = None
            failures = 0
            status = "reset"
        elif locked_until is not None:
            status = "refused"
        elif ev.kind == "fail":
            failures += 1
            if failures >= LOCK_AFTER:
                locked_until = ev.minute + LOCK_MINUTES
                status = "locked"
            else:
                status = "failed"
        else:
            failures = 0
            sessions += 1
            status = "ok"
        state[ev.user_id] = LoginState(
            failures=failures, locked_until=locked_until, sessions=sessions
        )
        out.append(
            LoginResult(
                id=ev.id,
                user_id=ev.user_id,
                minute=ev.minute,
                kind=ev.kind,
                status=status,
                sessions=sessions,
            )
        )
    return out


@endpoint("GET", "/logins/replay", auth=Admin)
def replay(user: CurrentUser) -> List:
    events = fetch(LoginEvents)
    ordered = sort(events, by=["id"])
    bounded = limit(ordered, n=100)
    results = call(replay_logins, events=bounded)
    return results
"""


def build_db(path: Path, events: list[dict]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT NOT NULL, token TEXT NOT NULL);
            CREATE TABLE login_events (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, minute INTEGER NOT NULL,
                kind TEXT NOT NULL
            );
            """
        )
        conn.execute("INSERT INTO users VALUES (1, 'Admin', ?)", (TOKEN,))
        conn.executemany(
            "INSERT INTO login_events VALUES (?, ?, ?, ?)",
            [(e["id"], e["user_id"], e["minute"], e["kind"]) for e in events],
        )
        conn.commit()
    finally:
        conn.close()
