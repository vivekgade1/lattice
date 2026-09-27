"""Held-out repos for the Lattice importer (I6a; see ``bench/import_repos/*/PROVENANCE.md``).

These apps are never read by anything under ``lattice/importer/`` -- only checked here
for "does it start and answer a request", the I6a gate. Each repo gets: the app
building in-process against a temp SQLite database, a smoke request (``/docs`` and an
authenticated list endpoint), a login, and one ownership check on its owned resource.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bench.import_repos.justnotes import make_app as make_justnotes_app
from bench.import_repos.justnotes.seed import (
    SEED_USER_CREDENTIALS as JUSTNOTES_CREDENTIALS,
)
from bench.import_repos.justnotes.seed import build_seed_db as build_justnotes_db
from bench.import_repos.taskflow import make_app as make_taskflow_app
from bench.import_repos.taskflow.seed import (
    SEED_USER_CREDENTIALS as TASKFLOW_CREDENTIALS,
)
from bench.import_repos.taskflow.seed import build_seed_db as build_taskflow_db

# --------------------------------------------------------------------- justnotes --


@pytest.fixture
def justnotes_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    db = build_justnotes_db(tmp_path / "justnotes.db")
    app = make_justnotes_app(db)
    with TestClient(app) as client:
        yield client
    app.state.engine.dispose()


def _justnotes_login(client: TestClient, username: str) -> str:
    password = JUSTNOTES_CREDENTIALS[username]
    response = client.post("/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_justnotes_app_starts_and_serves_docs(justnotes_client: TestClient) -> None:
    response = justnotes_client.get("/docs")
    assert response.status_code == 200
    paths = set(justnotes_client.get("/openapi.json").json()["paths"])
    assert {"/auth/login", "/auth/register", "/notes", "/notes/public", "/tags"} <= paths


def test_justnotes_health(justnotes_client: TestClient) -> None:
    assert justnotes_client.get("/health").status_code == 200


def test_justnotes_login_and_list_own_notes(justnotes_client: TestClient) -> None:
    token = _justnotes_login(justnotes_client, "alice")
    response = justnotes_client.get("/notes", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    notes = response.json()
    assert len(notes) == 2
    assert {n["title"] for n in notes} == {"Grocery list", "Trip notes"}
    assert all(n["owner"]["username"] == "alice" for n in notes)


def test_justnotes_public_notes_need_no_auth(justnotes_client: TestClient) -> None:
    response = justnotes_client.get("/notes/public")
    assert response.status_code == 200
    public = response.json()
    assert len(public) == 1
    assert public[0]["title"] == "Trip notes"


def test_justnotes_notes_require_auth(justnotes_client: TestClient) -> None:
    assert justnotes_client.get("/notes").status_code == 401


def test_justnotes_ownership_is_enforced(justnotes_client: TestClient) -> None:
    alice_token = _justnotes_login(justnotes_client, "alice")
    bob_token = _justnotes_login(justnotes_client, "bob")

    alice_notes = justnotes_client.get(
        "/notes", headers={"Authorization": f"Bearer {alice_token}"}
    ).json()
    alice_private_note_id = next(n["id"] for n in alice_notes if not n["isPublic"])

    response = justnotes_client.put(
        f"/notes/{alice_private_note_id}",
        headers={"Authorization": f"Bearer {bob_token}"},
        json={"title": "Hijacked", "text": "nope", "isPublic": False},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------- taskflow --


@pytest.fixture
def taskflow_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    db = build_taskflow_db(tmp_path / "taskflow.db")
    app = make_taskflow_app(db)
    with TestClient(app) as client:
        yield client
    app.state.engine.dispose()


def _taskflow_login(client: TestClient, email: str) -> str:
    password = TASKFLOW_CREDENTIALS[email]
    response = client.post(
        "/api/v1/auth/token", data={"username": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_taskflow_app_starts_and_serves_docs(taskflow_client: TestClient) -> None:
    response = taskflow_client.get("/docs")
    assert response.status_code == 200
    paths = set(taskflow_client.get("/openapi.json").json()["paths"])
    assert {"/api/v1/auth/register", "/api/v1/auth/token", "/api/v1/tasks"} <= paths


def test_taskflow_health(taskflow_client: TestClient) -> None:
    assert taskflow_client.get("/health").json() == {"status": "healthy"}


def test_taskflow_login_and_list_own_tasks(taskflow_client: TestClient) -> None:
    token = _taskflow_login(taskflow_client, "alice@example.com")
    response = taskflow_client.get(
        "/api/v1/tasks", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert {t["title"] for t in body["items"]} == {
        "Write onboarding doc",
        "Ship v2 API",
        "Retire legacy endpoint",
    }


def test_taskflow_tasks_require_auth(taskflow_client: TestClient) -> None:
    assert taskflow_client.get("/api/v1/tasks").status_code == 401


def test_taskflow_ownership_is_enforced(taskflow_client: TestClient) -> None:
    alice_token = _taskflow_login(taskflow_client, "alice@example.com")
    bob_token = _taskflow_login(taskflow_client, "bob@example.com")

    alice_tasks = taskflow_client.get(
        "/api/v1/tasks", headers={"Authorization": f"Bearer {alice_token}"}
    ).json()["items"]
    alice_task_id = alice_tasks[0]["id"]

    # Bob's task list never includes Alice's tasks.
    bob_tasks = taskflow_client.get(
        "/api/v1/tasks", headers={"Authorization": f"Bearer {bob_token}"}
    ).json()["items"]
    assert alice_task_id not in {t["id"] for t in bob_tasks}

    # A direct lookup by id is a consistent 404, not a 403 that would disclose it exists.
    response = taskflow_client.get(
        f"/api/v1/tasks/{alice_task_id}", headers={"Authorization": f"Bearer {bob_token}"}
    )
    assert response.status_code == 404

    response = taskflow_client.delete(
        f"/api/v1/tasks/{alice_task_id}", headers={"Authorization": f"Bearer {bob_token}"}
    )
    assert response.status_code == 404


def test_taskflow_create_and_update_task(taskflow_client: TestClient) -> None:
    token = _taskflow_login(taskflow_client, "alice@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = taskflow_client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "New task", "description": "Fresh.", "priority": "low"},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    updated = taskflow_client.patch(
        f"/api/v1/tasks/{task_id}", headers=headers, json={"status": "done"}
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "done"
