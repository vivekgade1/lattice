"""Verification normalises the app's own password-hash column (found by the suite's auth
spec), not the template's ``hashed_password``: a new salted hash differs on every call,
so a route that stores one (a registration) can only verify when both sides' new hashes
read ``<hash:new>``.

The synthetic app is tests/importer/test_import_int_keys.py's with the column renamed to
``password_digest``.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from typing import Any

import pytest

import lattice.importer.lift as lift
from lattice.importer.transport import AppRuntime
from lattice.importer.verify import Normalizer
from tests.importer.test_import_int_keys import FILES

PACKAGE = "shelf_digest_column"


def test_verification_normalises_the_apps_password_column(tmp_path, monkeypatch) -> None:
    seen: list[Any] = []
    real = lift.verify

    def spy(*args: Any, **kw: Any) -> Any:
        seen.append(kw.get("hash_columns"))
        return real(*args, **kw)

    monkeypatch.setattr(lift, "verify", spy)
    pkg = tmp_path / PACKAGE
    pkg.mkdir()
    for name, src in FILES.items():
        text = textwrap.dedent(src).replace("PKG", PACKAGE)
        (pkg / f"{name}.py").write_text(text.replace("hashed_password", "password_digest"))
    sys.path.insert(0, str(tmp_path))
    try:
        factory = importlib.import_module(PACKAGE).make_app
        result = lift.lift_repo(tmp_path, PACKAGE, factory, hypothesis_bodies=1)
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in sys.modules if m == PACKAGE or m.startswith(PACKAGE + ".")]:
            del sys.modules[name]
    assert seen and all(cols == ("password_digest",) for cols in seen), seen
    assert any(r.status == "lifted-verified" for r in result.results)


@pytest.mark.parametrize("column", ["password_digest", "password"])
def test_a_new_hash_in_the_named_column_is_normalised(tmp_path, column) -> None:
    import sqlite3

    db = tmp_path / "seed.db"
    conn = sqlite3.connect(db)
    conn.execute(f"CREATE TABLE users (id INTEGER PRIMARY KEY, {column} TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'seed-hash')")
    conn.commit()
    conn.close()
    norm = Normalizer(db, (column,)).fresh()
    assert norm.value("salted-1", column) == norm.value("salted-2", column) == "<hash:new>"
    assert norm.value("seed-hash", column) == "<hash:seed>"


def test_a_login_token_is_fetched_again_before_it_can_expire(monkeypatch) -> None:
    """The Lattice side's login reuses the original's token for a handle only briefly: a
    long import (many LLM rounds) outlives the app's token lifetime (TaskFlow's and
    JustNotes' are 30 minutes), and a stale token would fail every later case with 401."""
    from types import SimpleNamespace

    logins: list[str] = []
    suite = SimpleNamespace(
        login=lambda client, handle: logins.append(handle) or f"token-{len(logins)}",
        db_factory=lambda variant, path: path,
    )

    class App:
        state = SimpleNamespace()

    # the original is served by the context's runtime (W1-A: no module-level TestClient)
    runtime = AppRuntime(transport=lambda app, **_: _NullClient(app))
    ctx = SimpleNamespace(_tokens={}, app_factory=lambda db: App(), runtime=runtime)
    clock = [0.0]
    monkeypatch.setattr(lift, "_clock", lambda: clock[0])
    login = lift._login_tokens(ctx, suite)  # type: ignore[arg-type]
    assert login(None, "alice") == "token-1"
    clock[0] = lift.LOGIN_TOKEN_REUSE_SECONDS - 1
    assert login(None, "alice") == "token-1"  # reused
    clock[0] = lift.LOGIN_TOKEN_REUSE_SECONDS + 1
    assert login(None, "alice") == "token-2"  # fetched again


class _NullClient:
    client = None  # what the login is handed

    def __init__(self, app: Any) -> None:
        pass

    def __enter__(self) -> _NullClient:
        return self

    def __exit__(self, *exc: object) -> None:
        pass
