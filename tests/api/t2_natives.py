"""Native implementations for the T2 test program (tests/api/t2prog.py).

Standard library only: the codegen differential copies this file next to the generated
app as `natives.py`, and the generated app imports its functions by name. Procedures
call them directly (`native.slugify(...)`, `native.send_digest(...)`).

`send_digest` records every call in `SENT` (the runtime tests read it) and fails on a
title containing "boom".
"""

from __future__ import annotations

SENT: list[tuple[object, ...]] = []


def send_digest(to: str, text: str) -> None:
    if "BOOM" in text:
        raise ValueError("boom refused")
    SENT.append(("send_digest", to, text))


def slugify(text: str) -> str:
    return "-".join(text.lower().split())


NATIVES = {"send_digest": send_digest, "slugify": slugify}
