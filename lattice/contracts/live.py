"""The live contract check (decision 11): replay the provider's own 2xx spec step for each import
on a provider app that the check starts itself. **Not built yet: this is the interface only.**

The provider app is started by the check on a throwaway SQLite database (through
``start_target_harness``, in-process by default), never reached through a URL: a replay mutates
data. A link's ``live_url`` is refused (:class:`LiveError`, code ``not_loopback`` for a
non-loopback host, ``live_url_unsupported`` for a loopback one). The provider's ``app``, ``db``,
``login``, ``src`` and ``env`` come from its ``studio.toml`` when it has one, else from the
manifest's ``[[project]]`` keys (:meth:`lattice.workspace.WsProject.settings`; ``env`` through
:meth:`lattice.workspace.WsProject.expanded_env`).

Per import: the first 2xx step for that endpoint (scenario then step order) in the provider's
current spec, made by ``as_user`` (anonymous for ``public``, any handle for a token import
without ``as_user``), replayed up to that step on a fresh seed (earlier steps without
assertions). The answer is compared with the import, never with the pin:

* a status that is not in the import's ``status``: 401 or 403 give ``auth_changed``, anything
  else ``status_not_observed``;
* a 2xx without a relied response field: ``response_field_missing``.

Request fields are not checked (the replay sends the spec step's body, not the consumer's).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lattice.contracts.check import Import
    from lattice.workspace import Link, Workspace

LIVE_KINDS = ("status_not_observed", "auth_changed", "response_field_missing")


class LiveError(Exception):
    """The live check cannot run (the link's ``live_url`` is refused, the provider app does not
    start, ...): ``code`` is a short machine code, the message says why. The link is an error and
    the exit code is 2."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def run_live(
    workspace: Workspace,
    link: Link,
    imports: Sequence[Import],
    current_export: Mapping[str, Any],
    *,
    clone_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Replay the imports of `link` (``link.source`` -> ``link.target``, the imports already
    filtered to that provider) against the provider app and return::

        {
          "status": "ok",
          "replayed": 7,                      # imports that had a 2xx step and were replayed
          "not_replayed": [                   # sorted by endpoint
            {"endpoint": "GET /x", "reason": "no 2xx step for as_user eve"},
          ],
          "findings": [                       # BROKEN only, sorted by (endpoint, kind, item)
            {"class": "BROKEN", "provider": "scheduler", "endpoint": "POST /a/{}/runs",
             "kind": "status_not_observed",   # | "auth_changed" | "response_field_missing"
             "item": "201",                   # the status, "public" / "token" / "as_user:eve",
                                              # or the missing response field
             "detail": "the live answer was 200"},
          ],
          "verified_live": [                  # static GAPs the live answer satisfied
            {"provider": "scheduler", "endpoint": "POST /auth/login",
             "kind": "response_field_unobserved", "item": "access_token"},
          ],
        }

    `current_export` is the provider's current export (the ``export`` dict of
    :func:`lattice.contracts.export.compute_export`), from which the static gaps follow
    (:func:`lattice.contracts.check.claims_of` and :func:`~lattice.contracts.check.holds`).
    `clone_env` is extra environment for the provider app. Raises :class:`LiveError`.

    Not built: raises ``NotImplementedError``, which the check reports as ``live: not_built``.
    """
    raise NotImplementedError("live check not built")
