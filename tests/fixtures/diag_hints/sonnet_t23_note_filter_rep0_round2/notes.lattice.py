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


@endpoint("GET", "/notes", auth=Customer, response="NoteResponse")
def get_notes(
    user: CurrentUser,
    tag_id: Int(ge=1) | None = None,
    q: Str | None = None,
    limit: Int(ge=1, le=100) = 20,
    offset: Int(ge=0) = 0,
) -> Page | NotFound | Forbidden:
    tags = fetch(Tag)
    matching_tag = where(tags, col("id") == tag_id)
    tag_count = group(matching_tag, by=[], n=agg.count())
    tag_exists = require(tag_count, is_null(tag_id) | (col("n") > 0), else_=NotFound("Tag not found"))
    own_tag_rows = where(matching_tag, is_admin(user) | (col("owner_id") == user.id))
    owned_tag_count = group(own_tag_rows, by=[], n=agg.count())
    tag_authorized = require(
        owned_tag_count,
        is_null(tag_id) | (col("n") > 0),
        else_=Forbidden("Not authorized to access this tag"),
    )
    notes = fetch(Note)
    my_notes = where(notes, is_admin(user) | (col("owner_id") == user.id))
    links = fetch(NoteTagLink)
    tag_links = where(links, col("tag_id") == tag_id)
    joined = join(my_notes, tag_links, on={"id": "note_id"}, how="left")
    tag_filtered = where(joined, is_null(tag_id) | (col("tag_id") == tag_id))
    text_filtered = where(
        tag_filtered,
        is_null(q)
        | fn.contains(col("title"), sub=q, ignore_case=True)
        | fn.contains(col("text"), sub=q, ignore_case=True),
    )
    with_owner = derive(text_filtered, owner=user.username)
    ordered = sort(with_owner, by=["id"])
    shaped = select(ordered, cols=["title", "text", "isPublic", "id", "createdAt", "updatedAt", "owner"])
    result = page(shaped, limit=limit, offset=offset)
    return result
