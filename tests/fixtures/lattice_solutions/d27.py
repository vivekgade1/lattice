# lattice: v1
from lattice.ops import agg, coalesce, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
pending = where(orders, col("status") == "pending")
pending_by_customer = group(
    pending,
    by=["customer_id"],
    pending_total=agg.sum(col("qty") * col("unit_price")),
    oldest_pending_days=agg.max(
        fn.date_diff(
            fn.to_date("2024-12-31", format="%Y-%m-%d"), col("order_date"), unit="days"
        )
    ),
)
paid = where(orders, col("status") == "paid")
paid_by_customer = group(
    paid, by=["customer_id"], paid_total=agg.sum(col("qty") * col("unit_price"))
)
compared = join(pending_by_customer, paid_by_customer, on=["customer_id"], how="left")
backlogged = where(compared, col("pending_total") > coalesce(col("paid_total"), 0.0))
rounded = derive(
    backlogged,
    pending_value=fn.round(col("pending_total"), digits=2),
    paid_revenue=fn.round(coalesce(col("paid_total"), 0.0), digits=2),
)
result = select(
    rounded, cols=["customer_id", "pending_value", "paid_revenue", "oldest_pending_days"]
)
write(result, "out.parquet")
