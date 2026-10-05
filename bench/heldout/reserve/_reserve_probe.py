"""Boot-and-replay worker for the Phase 2 held-out reserve app (Lattice-owned, not upstream code).

    python -m bench.heldout.reserve._reserve_probe APP OUTDIR

For APP ``realworld``, in this process only (a copy of ``bench/heldout/_heldout_probe.py``,
itself a copy of ``bench/import_repos/_phase2_probe.py``, with the reserve app list):

1. seeds a template database ``OUTDIR/template.db`` (``<app>.seed.build_seed_db``);
2. on a fresh copy, boots the app (``<app>.make_app``) under a WSGI transport
   (``httpx.WSGITransport``, base URL ``http://testserver``);
3. logs in as every role in ``seed.ROLES`` through the app's own login
   (``seed.login_headers``) and sends ``seed.AUTH_PROBE`` as that role, anonymously and
   as every ``seed.REFUSED`` user;
4. replays every ``<app>.fidelity.SCENARIOS`` scenario on its own fresh copy.

Prints one JSON object on stdout. Every file it writes is under OUTDIR. Run one APP per
process (``bench/heldout/reserve/test_reserve_app.py`` does).
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

APPS = ("realworld",)


def _client(app: Any) -> Any:
    import httpx

    return httpx.Client(
        transport=httpx.WSGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    )


def _body(answer: Any) -> Any:
    try:
        return answer.json()
    except ValueError:
        return answer.text


def run(name: str, out: Path) -> dict[str, Any]:
    from lattice.spec.check import subset

    package = importlib.import_module(f"bench.import_repos.{name}")
    seed = importlib.import_module(f"bench.import_repos.{name}.seed")
    fidelity = importlib.import_module(f"bench.import_repos.{name}.fidelity")

    out.mkdir(parents=True, exist_ok=True)
    template = seed.build_seed_db(out / "template.db")
    copies = iter(range(10_000))

    def fresh() -> Path:
        db = out / f"case-{next(copies)}.db"
        shutil.copyfile(template, db)
        return db

    result: dict[str, Any] = {"app": name, "roles": {}, "refused": {}, "fidelity": []}

    app = package.make_app(fresh())
    try:
        with _client(app) as client:
            method, path, body = seed.AUTH_PROBE
            anon = client.request(method, path, json=body)
            result["anonymous"] = anon.status_code
            for role, handle in seed.ROLES.items():
                headers = seed.login_headers(client, handle)
                answer = client.request(method, path, json=body, headers=headers)
                result["roles"][role] = {"user": handle, "status": answer.status_code}
            for handle in seed.REFUSED:
                try:
                    headers = seed.login_headers(client, handle)
                except RuntimeError as err:
                    result["refused"][handle] = {"login": str(err)[:200]}
                    continue
                answer = client.request(method, path, json=body, headers=headers)
                result["refused"][handle] = {"status": answer.status_code}
    finally:
        package.dispose(app)

    for scenario in fidelity.SCENARIOS:
        problems: list[str] = []
        app = package.make_app(fresh())
        try:
            with _client(app) as client:
                tokens: dict[str, dict[str, str]] = {}
                for i, step in enumerate(scenario.steps, 1):
                    headers: dict[str, str] = {}
                    if step.user is not None:
                        if step.user not in tokens:
                            try:
                                tokens[step.user] = seed.login_headers(client, step.user)
                            except RuntimeError:
                                # a refused user: send their raw credentials where the
                                # app allows that, else no credentials at all
                                tokens[step.user] = _raw_credentials(seed, step.user)
                        headers = tokens[step.user]
                    answer = client.request(
                        step.method,
                        step.path,
                        json=step.json,
                        params=step.params,
                        headers=headers,
                    )
                    body = _body(answer)
                    if answer.status_code != step.expect_status:
                        problems.append(
                            f"step {i} {step.method} {step.path}: status {answer.status_code} "
                            f"(expected {step.expect_status}) body {str(body)[:300]}"
                        )
                    elif step.expect_body is not None and not subset(step.expect_body, body):
                        problems.append(
                            f"step {i} {step.method} {step.path}: body {str(body)[:300]} "
                            f"lacks {step.expect_body!r}"
                        )
        finally:
            package.dispose(app)
        result["fidelity"].append({"scenario": scenario.name, "problems": problems})
    return result


def _raw_credentials(seed: Any, handle: str) -> dict[str, str]:
    raw = getattr(seed, "raw_credentials_headers", None)
    return raw(handle) if raw is not None else {}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in APPS:
        print(f"usage: python -m bench.heldout.reserve._reserve_probe {{{','.join(APPS)}}} OUTDIR")
        return 2
    print(json.dumps(run(argv[0], Path(argv[1]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
