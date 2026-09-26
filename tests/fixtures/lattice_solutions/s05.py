# lattice: v1
from lattice.ops import coalesce, col, derive, load, select, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
priced = derive(orders, net_price=col("unit_price") * (1 - coalesce(col("discount"), 0.0)))
result = select(priced, cols=["order_id", "net_price"])
write(result, "out.parquet")
