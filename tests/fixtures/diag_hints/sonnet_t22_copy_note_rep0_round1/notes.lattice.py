# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.justnotes.app.api.notes by lattice.importer (deterministic lifters and the LLM loop: get_notes_extend, delete_note; every route verified).

@endpoint("POST", "/notes/{id}/copy", auth=Customer, response="NoteResponse")
def copy_note(id: Int, user: CurrentUser) -> Created | NotFound | Forbidden:
    notes = fetch(Note)
    found = where(notes, col("id") == id)
    note = one(found, else_=NotFound("Note not found"))
    checked = require(note, col("isPublic") | (col("owner_id") == user.id), else_=Forbidden("Not authorized to access this note"))
    copyTitle = fn.concat(checked.title, " (copy)")
    created = insert(Note, owner=user, title=copyTitle, text=checked.text, isPublic=False)
    withOwner = derive(created, owner=user.username)
    result = select(withOwner, cols=["title", "text", "isPublic", "id", "createdAt", "updatedAt", "owner"])
    return result


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
