# lattice: v1

@endpoint("POST", "/notes/{id}/copy", auth=Customer)
def copy_note(id: Int(ge=1), user: CurrentUser) -> Created | NotFound:
    notes = fetch(Note)
    found = where(notes, col("id") == id)
    
    prepared = derive(
        found,
        copy_title=col("title") + " (copy)",
        copy_text=col("text")
    )
    
    original = one(prepared, else_=NotFound("note"))
    
    copied = insert(
        Note,
        title=col("copy_title"),
        text=col("copy_text"),
        isPublic=False,
        owner_id=user.id,
        createdAt=1,
        updatedAt=1
    )
    
    result = select(copied, cols=["title", "text", "isPublic", "id", "createdAt", "updatedAt"])
    return result
