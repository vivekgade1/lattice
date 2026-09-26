# lattice: v1
from lattice.ops import agg, col, group, is_null, join, load, select, where, write
from lattice.schemas import Customers, Orders

customers = load("customers.csv", schema=Customers)
orders = load("orders.csv", schema=Orders)
order_counts = group(orders, by=["customer_id"], n_orders=agg.count())
with_counts = join(customers, order_counts, on=["customer_id"], how="left")
no_orders = where(with_counts, is_null(col("n_orders")))
result = select(no_orders, cols=["customer_id", "name"])
write(result, "out.parquet")
