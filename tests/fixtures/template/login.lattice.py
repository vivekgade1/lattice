# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# The FastAPI template's login route (bench/template_app/app/api/routes/login.py):
# an OAuth2 password form (username = the email) answered with a JWT access token.
@endpoint("POST", "/api/v1/login/access-token", auth=Public, response="Token", body="form")
def login_access_token(username: Str, password: Str) -> One | BadRequest:
    users = fetch(User)
    found = where(users, col("email") == username)
    account = one(found, else_=BadRequest("Incorrect email or password"))
    verified = require(
        account,
        native.verify_password(password, col("hashed_password")),
        else_=BadRequest("Incorrect email or password"),
    )
    active = require(verified, col("is_active"), else_=BadRequest("Inactive user"))
    token = derive(active, access_token=native.create_access_token(col("id")), token_type="bearer")
    shown = select(token, cols=["access_token", "token_type"])
    return shown
