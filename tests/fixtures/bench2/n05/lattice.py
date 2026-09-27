# lattice: v1
from lattice.ops import agg, col, group, join, load, where, write
from lattice.schemas import Customers2, Orders2

orders = load("orders2.csv", schema=Orders2)
customers = load("customers2.csv", schema=Customers2)
paid = where(orders, col("status") == "paid")
buyers = group(paid, by=["customer_id"], n_paid=agg.count())
with_region = join(buyers, customers, on=["customer_id"], how="inner")
per_region = group(with_region, by=["region"], n_buyers=agg.count())
write(per_region, "out.parquet")
