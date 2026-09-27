# lattice: v1
from lattice.ops import agg, col, group, join, load, where, write
from lattice.schemas import Customers2, Orders2

orders = load("orders2.csv", schema=Orders2)
customers = load("customers2.csv", schema=Customers2)
paid = where(orders, col("status") == "paid")
joined = join(paid, customers, on=["customer_id"], how="inner")
per_zip = group(joined, by=["zip"], paid_revenue=agg.sum(col("qty") * col("unit_price")))
write(per_zip, "out.parquet")
