# lattice: v1
from lattice.ops import agg, coalesce, col, derive, group, join, load, select, where, write
from lattice.schemas import Customers2, Orders2

customers = load("customers2.csv", schema=Customers2)
orders = load("orders2.csv", schema=Orders2)
paid = where(orders, col("status") == "paid")
counts = group(paid, by=["customer_id"], n=agg.count())
joined = join(customers, counts, on=["customer_id"], how="left")
filled = derive(joined, n_paid_orders=coalesce(col("n"), 0))
result = select(filled, cols=["customer_id", "zip", "n_paid_orders"])
write(result, "out.parquet")
