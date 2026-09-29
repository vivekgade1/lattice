# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.taskflow.app.auth_routes by lattice.importer (deterministic lifters and the LLM loop: register; every route verified).
@endpoint("POST", "/api/v1/auth/register", auth=Public, response="UserPublic")
def register(
    email: Email(max_len=320),
    full_name: Str(min_len=2, max_len=120),
    password: Str(min_len=8, max_len=128),
) -> Created | Conflict:
    users = fetch(User)
    same = where(users, col("email") == fn.lower(fn.strip(email)))
    existing = group(same, by=[], n=agg.count())
    fresh = require(existing, col("n") == 0, else_=Conflict("An account with this email already exists"))
    created = insert(
        User,
        email=fn.lower(fn.strip(email)),
        full_name=fn.strip(full_name),
        password_hash=native.hash_password(password),
        is_active=True,
        created_at=native.utc_now(),
    )
    public = select(created, cols=["id", "email", "full_name", "is_active", "created_at"])
    return public


@endpoint("GET", "/api/v1/auth/me", auth=Customer, response="UserPublic")
def read_current_user(user: CurrentUser) -> One:
    shown = select(user, cols=["id", "email", "full_name", "is_active", "created_at"])
    return shown
