# lattice: v1
from lattice.ops import agg, coalesce, col, derive, group, join, load, when, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
paid = where(orders, col("status") == "paid")
per_customer = group(paid, by=["customer_id"], revenue=agg.sum(col("qty") * col("unit_price")))
with_revenue = join(customers, per_customer, on=["customer_id"], how="left")
bucketed = derive(
    with_revenue,
    value_bucket=when(
        coalesce(col("revenue"), 0.0) <= 0.0,
        then="none",
        otherwise=when(
            col("revenue") < 1500,
            then="low",
            otherwise=when(col("revenue") < 4000, then="mid", otherwise="high"),
        ),
    ),
)
counts = group(bucketed, by=["tier", "value_bucket"], n_customers=agg.count())
write(counts, "out.parquet")
