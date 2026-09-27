# lattice: v1
from lattice.ops import agg, coalesce, col, group, load, write
from lattice.schemas import Orders2

orders = load("orders2.csv", schema=Orders2)
overall = group(
    orders,
    by=[],
    avg_net_unit_price=agg.mean(col("unit_price") * (1 - coalesce(col("discount"), 0.0))),
)
write(overall, "out.parquet")
