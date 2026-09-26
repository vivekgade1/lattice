# lattice: v1
from lattice.ops import agg, coalesce, col, derive, group, join, load, sort, where, write
from lattice.schemas import Customers, Orders

customers = load("customers.csv", schema=Customers)
orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
counts = group(paid, by=["customer_id"], n_paid=agg.count())
with_counts = join(customers, counts, on=["customer_id"], how="left")
filled = derive(with_counts, paid_orders=coalesce(col("n_paid"), 0))
distribution = group(filled, by=["paid_orders"], n_customers=agg.count())
ordered = sort(distribution, by=["paid_orders"])
write(ordered, "out.parquet")
