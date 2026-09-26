# lattice: v1
from lattice.ops import agg, coalesce, col, derive, fn, group, join, load, where, write
from lattice.schemas import Customers, Orders

customers = load("customers.csv", schema=Customers)
orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
counts = group(paid, by=["customer_id"], n_paid=agg.count())
with_counts = join(customers, counts, on=["customer_id"], how="left")
keyed = derive(
    with_counts,
    name_key=fn.lower(fn.strip(col("name"))),
    paid_orders=coalesce(col("n_paid"), 0),
)
by_name = group(
    keyed,
    by=["name_key"],
    n_customers=agg.count(),
    n_paid_orders=agg.sum(col("paid_orders")),
)
shared = where(by_name, col("n_customers") >= 2)
write(shared, "out.parquet")
