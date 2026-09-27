# lattice: v1
from lattice.ops import agg, group, join, load, write
from lattice.schemas import Customers2, Orders2

orders = load("orders2.csv", schema=Orders2)
customers = load("customers2.csv", schema=Customers2)
joined = join(orders, customers, on=["customer_id"], how="inner")
counts = group(joined, by=["region", "tier"], n_orders=agg.count())
write(counts, "out.parquet")
