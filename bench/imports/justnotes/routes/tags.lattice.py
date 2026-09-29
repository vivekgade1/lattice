# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.justnotes.app.api.tags by lattice.importer (deterministic lifters and the LLM loop: delete_tag, get_user_keywords, delete_keyword; every route verified).
@endpoint("DELETE", "/tags/{tag_id}", auth=Customer)
def delete_tag(tag_id: Int, user: CurrentUser) -> One | NotFound | Forbidden:
    tags = fetch(Tag)
    wanted = where(tags, col("id") == tag_id)
    tag = one(wanted, else_=NotFound("Tag not found"))
    checked = require(tag, owner_is(user), else_=Forbidden("Not authorized to delete this tag"))
    deleted = delete(checked)
    with_msg = derive(deleted, message="Tag deleted successfully")
    result = select(with_msg, cols=["message"])
    return result


@endpoint("GET", "/keywords", auth=Customer)
def get_user_keywords(user: CurrentUser) -> List:
    keywords = fetch(Keyword)
    own = mine(keywords, by=user)
    slim = select(own, cols=["id", "term", "is_main_topic", "createdAt"])
    ordered = sort(slim, by=["id"])
    result = limit(ordered, n=100)
    return result


@endpoint("DELETE", "/keywords/{keyword_id}", auth=Customer)
def delete_keyword(keyword_id: Int, user: CurrentUser) -> One | NotFound | Forbidden:
    keywords = fetch(Keyword)
    found = where(keywords, col("id") == keyword_id)
    keyword = one(found, else_=NotFound("Keyword not found"))
    allowed = require(keyword, owner_is(user), else_=Forbidden("Not authorized to delete this keyword"))
    deleted = delete(allowed)
    result = derive(deleted, message="Keyword deleted successfully")
    out = select(result, cols=["message"])
    return out
