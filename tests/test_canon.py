import pytest

from lattice.canon import canonical_source
from lattice.check import check_source
from lattice.parse import HEADER
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
    ]
)
CUSTOMERS = Schema.from_pairs([("customer_id", Scalar.INT), ("name", Scalar.STR)])
PROFILE = Profile({"Orders": ORDERS, "Customers": CUSTOMERS})

PROGRAMS = [
    # 1: minimal
    "# lattice: v1\nfrom lattice.ops import load, write\nfrom lattice.schemas import Orders\n"
    "x = load('orders.csv', schema=Orders)\nwrite(x, 'out.parquet')\n",
    # 2: pipeline with odd spacing and quotes
    "# lattice: v1\n"
    "from lattice.ops import load, where, derive, group, write, col, agg\n"
    "from lattice.schemas import Orders\n\n\n"
    'raw   =  load( "orders.csv" , schema = Orders )\n'
    "paid = where(raw, (col('status') == \"paid\"))\n"
    "t = derive(paid, total = col( 'qty' ) * col('price'))\n"
    "g = group(t, by=[ 'region' ], revenue=agg.sum(col('total')), n=agg.count())\n"
    "write(g, 'revenue.parquet')\n",
    # 3: step
    "# lattice: v1\n"
    "from lattice.ops import load, where, write, col, step, Table, Int\n"
    "from lattice.schemas import Orders\n"
    "@step\n"
    "def big(t: Table[Orders], n: Int) -> Table:\n"
    "    y = where(t, col('qty') > n)\n"
    "    return y\n"
    "raw = load('orders.csv', schema=Orders)\n"
    "b = big(raw, n=-2)\n"
    "write(b, 'o.csv')\n",
    # 4: join/sort/limit/select/rename
    "# lattice: v1\n"
    "from lattice.ops import load, join, sort, limit, select, rename, write\n"
    "from lattice.schemas import Orders, Customers\n"
    "o = load('orders.csv', schema=Orders)\n"
    "c = load('customers.csv', schema=Customers)\n"
    "j = join(o, c, on=['customer_id'], how='left')\n"
    "s = sort(j, by=['qty', 'name'], desc=True)\n"
    "l = limit(s, n=5)\n"
    "p = select(l, cols=('name', 'qty'))\n"
    "r = rename(p, name='customer')\n"
    "write(r, 'o.parquet')\n",
    # 5: when / fn / coalesce / is_null / operators
    "# lattice: v1\n"
    "from lattice.ops import load, derive, where, write, col, when, fn, coalesce, is_null\n"
    "from lattice.schemas import Orders\n"
    "o = load('orders.csv', schema=Orders)\n"
    "d = derive(o, tier=when(col('qty') > 5, then='big', otherwise=when(col('qty') > 1, "
    "then='mid', otherwise='small')), up=fn.upper(col('region')), "
    "p=coalesce(col('price'), 0.0), neg=-(col('qty') % 3), "
    "r=fn.round(col('price') / 2, digits=1))\n"
    "w = where(d, ~is_null(col('price')) & ((col('qty') >= 1) | (col('tier') != 'small')))\n"
    "write(w, 'o.csv')\n",
]


def _check_ok(src):
    r = check_source(src, PROFILE)
    assert r.ok, r.diagnostics
    assert not [d for d in r.diagnostics if d.severity == "error"]
    return r.program


@pytest.mark.parametrize("src", PROGRAMS)
def test_fixpoint(src):
    once = canonical_source(_check_ok(src))
    assert once.startswith(HEADER + "\n")
    twice = canonical_source(_check_ok(once))
    assert twice == once


def test_normalizes_quotes_and_spacing():
    out = canonical_source(_check_ok(PROGRAMS[2 - 1]))
    assert "col('qty') * col('price')" in out
    assert "raw = load('orders.csv', schema=Orders)" in out
    assert "\n\n\n" not in out
    assert out.endswith("\n") and not out.endswith("\n\n")


def test_col_spacing():
    src = PROGRAMS[1].replace("col( 'qty' )", "col(  'qty'   )")
    assert "col('qty')" in canonical_source(_check_ok(src))
