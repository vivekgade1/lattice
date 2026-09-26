# lattice: v1
from lattice.ops import agg, col, derive, group, load, when, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
flagged = derive(orders, is_cancelled=when(col("status") == "cancelled", then=1, otherwise=0))
per_customer = group(
    flagged,
    by=["customer_id"],
    total_orders=agg.count(),
    cancelled_orders=agg.sum(col("is_cancelled")),
)
rates = derive(per_customer, cancel_rate=col("cancelled_orders") / col("total_orders"))
write(rates, "out.parquet")
