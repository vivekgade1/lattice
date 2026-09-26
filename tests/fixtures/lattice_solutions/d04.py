# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, sort, when, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
paid_2024 = where(orders, (col("status") == "paid") & (fn.year(col("order_date")) == 2024))
joined = join(paid_2024, customers, on=["customer_id"], how="inner")
flagged = derive(
    joined,
    order_month=fn.month(col("order_date")),
    is_new=when(fn.year(col("signup_date")) == 2024, then=1, otherwise=0),
)
monthly = group(flagged, by=["order_month"], n_orders=agg.count(), n_new=agg.sum(col("is_new")))
shares = derive(monthly, new_customer_share=fn.round(col("n_new") / col("n_orders"), digits=3))
result = select(shares, cols=["order_month", "n_orders", "new_customer_share"])
ordered = sort(result, by=["order_month"])
write(ordered, "out.parquet")
