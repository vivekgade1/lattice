"""The auth of an app imported through a Django or Flask front end, as its generated
profile names it (Phase 2 W3).

Such an app authenticates callers with its framework's own machinery (DRF
authentication classes, Flask-HTTPAuth callbacks, a session cookie), which the importer
does not lift. The profile still needs a users table and an auth scheme for its rule
layer (layer R probes the original app, never this), so its ``NativeToken`` names a
decoder that calls :func:`not_lifted`: serving a Lattice program on that profile fails
loudly on the first authenticated request instead of guessing a caller.
"""

from __future__ import annotations


class AuthNotLifted(RuntimeError):
    """The app's own auth was not lifted into Lattice."""


def not_lifted(framework: str, scheme: str, token: str) -> None:
    """Refuse to decode ``token``: the app's ``scheme`` auth on ``framework`` is not
    lifted (see the module docstring)."""
    raise AuthNotLifted(
        f"the app's own {scheme} auth ({framework}) is not lifted into Lattice: a program on "
        "this profile cannot authenticate callers (the rule layer probes the original app)"
    )


__all__ = ["AuthNotLifted", "not_lifted"]
