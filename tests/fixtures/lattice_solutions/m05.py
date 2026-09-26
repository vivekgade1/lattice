# lattice: v1
from lattice.ops import agg, coalesce, col, derive, group, load, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
flagged = derive(orders, has_discount=coalesce(col("discount"), 0.0) > 0)
summary = group(
    flagged,
    by=["has_discount"],
    n_orders=agg.count(),
    mean_unit_price=agg.mean(col("unit_price")),
)
write(summary, "out.parquet")
