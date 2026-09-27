"""NativeContext: app configuration for natives (``ApiProfile.settings``).

A native that declares settings (``NativeSig(settings=("secret_key",))``) is called
with a read-only ``NativeContext`` -- a ``types.MappingProxyType`` of exactly the
settings it declared -- as its first argument, then its Lattice arguments. The values
are the deployment's: supplied once when the app starts (``build_app(...,
settings=...)`` for the runtime; the natives module's ``SETTINGS`` for a generated
app), checked against the declared scalar types there, never per request and never
part of the program. So a JWT decoder can read its signing secret the way the original
app read ``request.app.state.settings.secret_key``, without the native importing the
request or module state.

The code after the marker is copied verbatim into generated FastAPI apps
(``lattice.api.codegen``), so both backends bind natives identically.
"""

from __future__ import annotations

# ---- begin (copied into generated apps)


def native_context(native: str, wanted: tuple[tuple[str, str], ...], settings: object) -> object:
    """The read-only NativeContext of `native`: its declared settings (name, scalar) out
    of the supplied `settings` mapping. A missing or mistyped value is a ValueError --
    at start-up, before serving anything."""
    import types

    kinds = {"Str": (str,), "Int": (int,), "Float": (int, float), "Bool": (bool,)}
    values: dict[str, object] = {}
    for name, scalar in wanted:
        if not hasattr(settings, "keys") or name not in settings:  # type: ignore[operator]
            raise ValueError(
                f"native {native!r} reads the setting {name!r}: supply it at start-up "
                f"(settings={{{name!r}: ...}})"
            )
        value = settings[name]  # type: ignore[index]
        if not isinstance(value, kinds[scalar]) or (scalar != "Bool" and isinstance(value, bool)):
            raise ValueError(f"setting {name!r} must be a {scalar}, not {type(value).__name__}")
        values[name] = value
    return types.MappingProxyType(values)


def with_context(fn: object, ctx: object) -> object:
    """`fn` called with the NativeContext `ctx` first."""
    import functools

    return functools.partial(fn, ctx)  # type: ignore[arg-type]


def bind_settings(
    natives: dict[str, object],
    declared: tuple[tuple[str, tuple[tuple[str, str], ...]], ...],
    settings: object,
) -> dict[str, object]:
    """`natives` with every one that reads settings (`declared`: native -> its
    (setting, scalar) pairs) bound to its NativeContext."""
    out = dict(natives)
    for name, wanted in declared:
        if name in out:
            out[name] = with_context(out[name], native_context(name, wanted, settings))
    return out
