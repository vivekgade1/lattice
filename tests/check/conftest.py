import pytest

from lattice.check import check_source
from lattice.profile import Profile
from lattice.typesys import Scalar, Schema

ORDERS = Schema.from_pairs(
    [
        ("order_id", Scalar.INT),
        ("customer_id", Scalar.INT),
        ("status", Scalar.STR),
        ("qty", Scalar.INT),
        ("price", Scalar.FLOAT),
        ("region", Scalar.STR),
        ("order_date", Scalar.DATE),
    ]
)
CUSTOMERS = Schema.from_pairs(
    [("customer_id", Scalar.INT), ("name", Scalar.STR), ("city", Scalar.STR)]
)

REGION_REVENUE = Schema.from_pairs([("region", Scalar.STR), ("revenue", Scalar.FLOAT)])


@pytest.fixture
def profile() -> Profile:
    return Profile(
        schemas={"Orders": ORDERS, "Customers": CUSTOMERS, "RegionRevenue": REGION_REVENUE}
    )


@pytest.fixture
def diags(profile):
    """Check a source and return its diagnostics."""

    def run(src: str):
        return check_source(src, profile).diagnostics

    return run


@pytest.fixture
def codes(diags):
    """Check a source and return its diagnostic codes in order."""

    def run(src: str) -> list[str]:
        return [d.code for d in diags(src)]

    return run


@pytest.fixture
def program(profile):
    """Build a full program from a body: header plus exactly the imports it uses."""
    import ast

    from lattice.catalog import IMPORTABLE

    def build(body: str) -> str:
        used = {n.id for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Name)}
        ops = sorted(used & IMPORTABLE)
        schemas = sorted(used & set(profile.schemas))
        out = "# lattice: v1\n"
        if ops:
            out += f"from lattice.ops import {', '.join(ops)}\n"
        if schemas:
            out += f"from lattice.schemas import {', '.join(schemas)}\n"
        return out + body

    return build
