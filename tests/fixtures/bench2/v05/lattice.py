# lattice: v1
from lattice.ops import agg, coalesce, col, group, load, write
from lattice.schemas import Orders2

orders = load("orders2.csv", schema=Orders2)
per_status = group(
    orders,
    by=["status"],
    n_orders=agg.count(),
    avg_rating=agg.mean(coalesce(col("rating"), 0.0)),
)
write(per_status, "out.parquet")
