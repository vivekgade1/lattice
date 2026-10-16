"""A tiny harness module: the references a check invocation names."""


def make_app(db):
    return ("app", db)


def build_db(db):
    return ("db", db)


def build_seed_db(db):
    return ("seed", db)


def login(client, handle):
    return "token"


RUNTIME = "a-runtime"
SERVICES = {"worker": lambda db: None, "scheduler": lambda db: None}
