"""Loop corpus 12 -- NATIVE ISLAND: replay login events through a lockout state machine.

Loop shape: one pass over ordered events carrying several interacting accumulators
            per user (consecutive failures, a lock deadline, a session count, a
            status) whose next values depend on the current state and the event kind.
Construct:  none -- this stays a native function (see README.md). The checker rejects
            the loop (L127) with a hint that names a native function.
Targets:    none (the original is the implementation).
"""

from __future__ import annotations

import random

SHAPE = "multi-accumulator state machine"
CONSTRUCT = "native function (not expressible)"
HINT = "native function"  # what the L127 hint on the original loop names
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
