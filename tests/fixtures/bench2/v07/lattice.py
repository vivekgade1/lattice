# lattice: v1
from lattice.ops import agg, coalesce, col, derive, group, load, write
from lattice.schemas import Orders2

orders = load("orders2.csv", schema=Orders2)
priced = derive(
    orders, net_unit_price=col("unit_price") * (1 - coalesce(col("discount"), 0.0))
)
per_product = group(
    priced, by=["product_id"], min_net_unit_price=agg.min(col("net_unit_price"))
)
write(per_product, "out.parquet")
