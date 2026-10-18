"""A harness whose database builder fails."""

from __future__ import annotations


def build_db(path):
    raise RuntimeError("seed data is missing")


def make_app(db_path):
    raise NotImplementedError
