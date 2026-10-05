"""Valid values for formatted fields (post-hoc round 2, 2026-10-05): a field of a text
format (Django's ``SlugField``, ``URLField``, ``GenericIPAddressField``, ``EmailField`` and
their DRF serializer fields; the ``<slug:...>`` path converter) only takes values of it.
The seed and the probes gave such a field generic text ("Slug 01"), so H1 drf_blog's
``{slug}`` routes answered the router's 404 to every probe -- read as "anonymous
admitted", refuting their auth.

* the seed writes values of the column's format, distinct per row, within its length;
* a request parameter of a format gets an example of it, and the probes' boundary
  values keep the format (a URL of the bound's length), or say no value of the format
  reaches the bound (an IP address).

The Django front end's reading of formats is tested on the DRF toy
(``django/test_posthoc_drf_toy.py``)."""

from __future__ import annotations

import pytest

from lattice.importer.formats import SHAPES, example, seed_value
from lattice.importer.suite import AuthSpec, ColumnSpec, TableSpec, seed_plan
from lattice.spec import ContractParam
from lattice.spec.probes import _edges, _sized
from lattice.typesys import Scalar


@pytest.mark.parametrize("fmt", ["slug", "url", "ip", "email"])
def test_seed_values_have_the_format_and_differ_per_row(fmt):
    values = [seed_value(fmt, f"Slug {i + 1:02d}", i) for i in range(8)]
    assert all(SHAPES[fmt].match(v) for v in values), values
    assert len(set(values)) == len(values)


def test_a_slug_keeps_within_its_length_and_stays_distinct():
    values = [seed_value("slug", f"A Rather Long Title {i + 1:02d}", i, 8) for i in range(12)]
    assert all(len(v) <= 8 and SHAPES["slug"].match(v) for v in values), values
    assert len(set(values)) == len(values)


def test_the_seed_plan_writes_slugs_and_urls():
    page = TableSpec("pages", (
        ColumnSpec("id", "int", primary_key=True),
        ColumnSpec("slug", "str", unique=True, max_len=50, format="slug"),
        ColumnSpec("link", "str", format="url"),
        ColumnSpec("title", "str"),
    ))  # fmt: skip
    users = TableSpec("users", (
        ColumnSpec("id", "int", primary_key=True), ColumnSpec("email", "email"),
        ColumnSpec("hashed_password", "str"), ColumnSpec("is_active", "bool"),
        ColumnSpec("is_superuser", "bool"),
    ))  # fmt: skip
    plan = seed_plan([users, page], AuthSpec("users", key_type="int"))
    rows = plan.rows["pages"]
    assert rows and all(SHAPES["slug"].match(r["slug"]) for r in rows), rows
    assert all(r["link"] is None or SHAPES["url"].match(r["link"]) for r in rows), rows
    assert not SHAPES["slug"].match(rows[0]["title"])  # plain text stays as it was


def test_a_parameter_of_a_format_gets_an_example_of_it():
    assert SHAPES["slug"].match(example("slug"))
    assert example("slug", 4) == "vali"
    assert SHAPES["url"].match(example("url"))
    assert example(None) is None


def test_boundary_values_keep_the_parameters_format():
    url = ContractParam("link", "body", Scalar.STR, max_len=200, example=example("url"))
    inside, beyond = _edges(url, "max_len", 200)
    assert len(inside) == 200 and len(beyond) == 201
    assert SHAPES["url"].match(inside) and SHAPES["url"].match(beyond)
    slug = ContractParam("slug", "body", Scalar.STR, max_len=50, example=example("slug"))
    assert _edges(slug, "max_len", 50) == ("b" * 50, "b" * 51)
    ip = ContractParam("addr", "body", Scalar.STR, max_len=39, example=example("ip"))
    assert _sized(ip, 39) is None  # no IPv4 address has 39 characters: unreachable
