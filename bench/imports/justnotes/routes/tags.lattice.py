# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.justnotes.app.api.tags by lattice.importer (deterministic lifters and the LLM loop: delete_tag, delete_keyword; every route verified).
@endpoint("DELETE", "/tags/{tag_id}", auth=Customer)
def delete_tag(tag_id: Int, user: CurrentUser) -> One | NotFound | Forbidden:
    tags = fetch(Tag)
    found = where(tags, col("id") == tag_id)
    tag = one(found, else_=NotFound("Tag not found"))
    checked = require(tag, col("owner_id") == user.id, else_=Forbidden("Not authorized to delete this tag"))
    deleted = delete(checked)
    result = derive(deleted, message="Tag deleted successfully")
    slim = select(result, cols=["message"])
    return slim


@endpoint("DELETE", "/keywords/{keyword_id}", auth=Customer)
def delete_keyword(keyword_id: Int, user: CurrentUser) -> One | NotFound | Forbidden:
    keywords = fetch(Keyword)
    wanted = where(keywords, col("id") == keyword_id)
    keyword = one(wanted, else_=NotFound("Keyword not found"))
    checked = require(keyword, col("owner_id") == user.id, else_=Forbidden("Not authorized to delete this keyword"))
    deleted = delete(checked)
    tagged = derive(deleted, message="Keyword deleted successfully")
    result = select(tagged, cols=["message"])
    return result
