# lattice: v1
from lattice.ops import agg, coalesce, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
paid = where(orders, col("status") == "paid")
joined = join(paid, customers, on=["customer_id"], how="inner")
valued = derive(
    joined,
    net=col("qty") * col("unit_price") * (1 - coalesce(col("discount"), 0.0)),
    k=1,
)
by_tier = group(valued, by=["tier", "k"], net_total=agg.sum(col("net")), n_orders=agg.count())
grand = group(by_tier, by=["k"], all_net=agg.sum(col("net_total")))
combined = join(by_tier, grand, on=["k"], how="inner")
shares = derive(
    combined,
    net_revenue=fn.round(col("net_total"), digits=2),
    revenue_share=fn.round(col("net_total") / col("all_net"), digits=4),
    avg_order_net=fn.round(col("net_total") / col("n_orders"), digits=2),
)
result = select(shares, cols=["tier", "net_revenue", "revenue_share", "avg_order_net"])
write(result, "out.parquet")
