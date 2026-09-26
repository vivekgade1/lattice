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


@pytest.fixture
def profile() -> Profile:
    return Profile(schemas={"Orders": ORDERS, "Customers": CUSTOMERS})


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
