# ruff: noqa
from sqlmodel import create_engine,Session
from bench.import_repos.multivendor.app.core.config import settings

engine = create_engine(settings.database_url, echo=True)

def get_session():
    with Session(engine) as session:
        yield session