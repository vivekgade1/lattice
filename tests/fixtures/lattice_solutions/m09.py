# lattice: v1
from lattice.ops import agg, coalesce, col, derive, fn, group, load, select, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
with_net = derive(orders, net=col("qty") * col("unit_price") * (1 - coalesce(col("discount"), 0.0)))
per_status = group(with_net, by=["status"], net_total=agg.sum(col("net")))
rounded = derive(per_status, net_revenue=fn.round(col("net_total"), digits=2))
result = select(rounded, cols=["status", "net_revenue"])
write(result, "out.parquet")
