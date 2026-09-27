# lattice: v1
from lattice.ops import agg, coalesce, col, group, load, write
from lattice.schemas import Orders2

orders = load("orders2.csv", schema=Orders2)
per_customer = group(
    orders, by=["customer_id"], lowest_rating=agg.min(coalesce(col("rating"), 0.0))
)
write(per_customer, "out.parquet")
