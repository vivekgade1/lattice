# lattice: v1
from lattice.ops import agg, col, group, join, load, write
from lattice.schemas import Customers2, Orders2

orders = load("orders2.csv", schema=Orders2)
customers = load("customers2.csv", schema=Customers2)
joined = join(orders, customers, on=["customer_id"], how="inner")
per_region = group(joined, by=["region"], avg_qty=agg.mean(col("qty")))
write(per_region, "out.parquet")
