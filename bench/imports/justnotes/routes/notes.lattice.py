# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.justnotes.app.api.notes by lattice.importer (deterministic lifters and the LLM loop: get_notes_summarize, get_notes_related, get_note, delete_note; every route verified).
@endpoint("GET", "/notes/{id}/summarize", auth=Customer)
def get_notes_summarize(id: Int, user: CurrentUser) -> One | NotFound | Forbidden:
    notes = fetch(Note)
    found = where(notes, col("id") == id)
    note = one(found, else_=NotFound("Note not found"))
    allowed = require(
        note,
        owner_is(user),
        else_=Forbidden("Not authorized to access this note"),
    )
    slim = select(allowed, cols=["id", "title", "text"])
    return slim


@endpoint("GET", "/notes/{id}/related", auth=Customer)
def get_notes_related(id: Int, user: CurrentUser) -> One | NotFound | Forbidden:
    notes = fetch(Note)
    wanted = where(notes, col("id") == id)
    note = one(wanted, else_=NotFound("Note not found"))
    allowed = require(
        note,
        owner_is(user),
        else_=Forbidden("Not authorized to access this note"),
    )
    all_notes = fetch(Note)
    own = mine(all_notes, by=user)
    counts = group(own, by=[], n=agg.count())
    result = derive(counts, message="Not enough notes to find relationships")
    slim = select(result, cols=["message"])
    return one(slim, else_=NotFound("Note not found"))


@endpoint("GET", "/notes/{id}", auth=Customer, response="NoteResponse")
def get_note(id: Int, user: CurrentUser) -> One | NotFound:
    all_notes = fetch(Note)
    notes = accessible(all_notes, by=user)
    wanted = where(notes, col("id") == id)
    note = one(wanted, else_=NotFound("Note not found"))
    shaped = derive(note, owner=user.id)
    result = select(
        shaped,
        cols=["title", "text", "isPublic", "id", "createdAt", "updatedAt", "owner"],
    )
    return result


@endpoint("DELETE", "/notes/{id}", auth=Customer)
def delete_note(id: Int, user: CurrentUser) -> One | NotFound | Forbidden:
    notes = fetch(Note)
    wanted = where(notes, col("id") == id)
    note = one(wanted, else_=NotFound("Note not found"))
    checked = require(note, owner_is(user), else_=Forbidden("Not authorized to delete this note"))
    deleted = delete(checked)
    result = derive(deleted, message="Note deleted successfully")
    slim = select(result, cols=["message"])
    return slim
