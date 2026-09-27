# lattice: v1
from lattice.ops import agg, coalesce, col, group, load, write
from lattice.schemas import Orders2

orders = load("orders2.csv", schema=Orders2)
per_product = group(
    orders, by=["product_id"], avg_rating=agg.mean(coalesce(col("rating"), 0.0))
)
write(per_product, "out.parquet")
