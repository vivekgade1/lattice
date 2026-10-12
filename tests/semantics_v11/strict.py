"""The strict tagged form a Python result is compared in (used by test_python.py)."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def strict(v: object) -> object:  # noqa: PLR0911
    """A Python result in the strict tagged form both targets' results are compared in:
    (tag, value) trees where bool, int and float differ, a float is its repr (-0.0 is not
    0.0), lists and tuples differ, dicts and sets are their items in order, a record is
    its name and fields, and an error is its code or kind and its message."""
    import dataclasses  # noqa: PLC0415

    from lattice.t2.support import T2Set  # noqa: PLC0415

    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, int):
        return ("int", v)
    if isinstance(v, float):
        return ("float", repr(v))
    if isinstance(v, str):
        return ("str", v)
    if v is None:
        return ("none",)
    if isinstance(v, datetime):
        return ("datetime", v.isoformat())
    if isinstance(v, date):
        return ("date", v.isoformat())
    if isinstance(v, timedelta):
        return ("duration", v.days, v.seconds, v.microseconds)
    if isinstance(v, list):
        return ("list", tuple(strict(x) for x in v))
    if isinstance(v, tuple):
        return ("tuple", tuple(strict(x) for x in v))
    if isinstance(v, dict):
        return ("dict", tuple((strict(k), strict(x)) for k, x in v.items()))
    if isinstance(v, T2Set | set | frozenset):
        return ("set", tuple(strict(x) for x in v))
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        fields = tuple(strict(getattr(v, f.name)) for f in dataclasses.fields(v))
        return ("record", type(v).__name__, fields)
    raise ValueError(f"no strict form for {v!r}")
