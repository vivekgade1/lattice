"""Native implementations for the for_each test profile (tests/api/effects.py).

Standard library only: the codegen differential copies this file next to the generated
app as `natives.py`, and the generated app imports its functions by name.

The effect natives record every call in `SENT` (in process: the runtime tests read it)
and fail on the text "boom", so a failed dispatch can be provoked from a request.
"""

from __future__ import annotations

import datetime as dt

SENT: list[tuple[object, ...]] = []  # (native name, *arguments), in call order
FAIL_ALWAYS: set[str] = set()  # natives that fail on every call (retry tests toggle it)


def _record(name: str, *args: object) -> None:
    if name in FAIL_ALWAYS:
        raise RuntimeError(f"{name} is down")
    if "boom" in args:
        raise ValueError("boom refused")
    SENT.append((name, *args))


def send_welcome(email: str, full_name: str) -> None:
    _record("send_welcome", email, full_name)


def item_created(item_id: int, title: str, done: bool) -> None:
    assert isinstance(item_id, int) and isinstance(done, bool)
    _record("item_created", item_id, title, done)


def item_viewed(item_id: int, title: str, note: str) -> None:
    _record("item_viewed", item_id, title, note)


def item_done(item_id: int, due: dt.date | None) -> None:
    assert due is None or isinstance(due, dt.date)
    _record("item_done", item_id, due)


def shout(text: str) -> str:
    return text.upper() + "!"


NATIVES = {
    "send_welcome": send_welcome,
    "item_created": item_created,
    "item_viewed": item_viewed,
    "item_done": item_done,
    "shout": shout,
}
