# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.justnotes.app.api.notes by lattice.importer (deterministic lifters and the LLM loop: get_notes_extend, delete_note; every route verified).
@endpoint("GET", "/notes/{id}/extend", auth=Customer)
def get_notes_extend(id: Int, user: CurrentUser) -> One | NotFound | Forbidden:
    notes = fetch(Note)
    found = where(notes, col("id") == id)
    note = one(found, else_=NotFound("Note not found"))
    checked = require(note, col("isPublic"), else_=Forbidden("Not authorized to access this note"))
    result = select(checked, cols=["title", "text"])
    return result


@endpoint("DELETE", "/notes/{id}", auth=Customer)
def delete_note(id: Int, user: CurrentUser) -> One | NotFound:
    notes = fetch(Note)
    found = where(notes, col("id") == id)
    note = one(found, else_=NotFound("Note not found"))
    deleted = delete(note)
    withMsg = derive(deleted, message="Note deleted successfully")
    result = select(withMsg, cols=["message"])
    return result
