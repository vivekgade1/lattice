# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.multivendor.app.routers.auth_router by lattice.importer (deterministic lifters and the LLM loop: register; every route verified).
@endpoint("POST", "/auth/register", auth=Public)
def register(
    full_name: Str(min_len=0, max_len=255),
    email: Email(max_len=255),
    password: Str(min_len=0, max_len=255),
) -> Created | BadRequest:
    users = fetch(User)
    matching = where(users, col("email") == email)
    counted = group(matching, by=[], n=agg.count())
    checked = require(counted, col("n") == 0, else_=BadRequest("Email already registered"))
    hashed = native.hash_password(password)
    created = insert(
        User,
        full_name=full_name,
        email=email,
        hashed_password=hashed,
        role="buyer",
        is_active=True,
        created_at=fn.now(),
    )
    result = select(created, cols=["id", "full_name", "email", "role", "is_active", "created_at"])
    return result
