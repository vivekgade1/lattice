"""A tiny in-memory notes service: the T1-3 driver's positive control (no database)."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class NoteIn(BaseModel):
    text: str


def create_app() -> FastAPI:
    app = FastAPI(title="minisvc")
    notes: dict[int, dict] = {}

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/notes")
    def list_notes() -> list[dict]:
        return [notes[k] for k in sorted(notes)]

    @app.post("/notes", status_code=201)
    def add_note(body: NoteIn) -> dict:
        if not body.text.strip():
            raise HTTPException(422, "text must not be blank")
        nid = max(notes, default=0) + 1
        notes[nid] = {"id": nid, "text": body.text}
        return notes[nid]

    @app.get("/notes/{note_id}")
    def get_note(note_id: int) -> dict:
        if note_id not in notes:
            raise HTTPException(404, "no such note")
        return notes[note_id]

    @app.delete("/notes/{note_id}", status_code=204)
    def delete_note(note_id: int) -> None:
        if notes.pop(note_id, None) is None:
            raise HTTPException(404, "no such note")

    return app
