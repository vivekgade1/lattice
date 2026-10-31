"""The app's own tests (held out from the agent in the T1-3 dry run)."""

from fastapi.testclient import TestClient
from minisvc.server import create_app


def client() -> TestClient:
    return TestClient(create_app())


def test_health():
    assert client().get("/health").json() == {"ok": True}


def test_empty_list():
    assert client().get("/notes").json() == []


def test_add_then_get():
    c = client()
    r = c.post("/notes", json={"text": "hi"})
    assert r.status_code == 201 and r.json() == {"id": 1, "text": "hi"}
    assert c.get("/notes/1").json() == {"id": 1, "text": "hi"}


def test_blank_text_is_422():
    assert client().post("/notes", json={"text": "  "}).status_code == 422


def test_missing_is_404():
    assert client().get("/notes/9").status_code == 404


def test_delete():
    c = client()
    c.post("/notes", json={"text": "a"})
    assert c.delete("/notes/1").status_code == 204
    assert c.get("/notes").json() == []
    assert c.delete("/notes/1").status_code == 404
