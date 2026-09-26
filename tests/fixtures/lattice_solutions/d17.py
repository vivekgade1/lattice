# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, when, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
paid = where(orders, col("status") == "paid")
per_customer = group(
    paid,
    by=["customer_id"],
    paid_orders_2023=agg.sum(when(fn.year(col("order_date")) == 2023, then=1, otherwise=0)),
    paid_orders_2024=agg.sum(when(fn.year(col("order_date")) == 2024, then=1, otherwise=0)),
)
lapsed = where(per_customer, (col("paid_orders_2023") > 0) & (col("paid_orders_2024") == 0))
named = join(lapsed, customers, on=["customer_id"], how="inner")
cleaned = derive(named, name_clean=fn.strip(col("name")))
result = select(cleaned, cols=["customer_id", "name_clean", "paid_orders_2023"])
write(result, "out.parquet")
