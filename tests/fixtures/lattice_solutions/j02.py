# lattice: v1
from lattice.ops import agg, col, group, join, load, select, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
regions = select(customers, cols=["customer_id", "region"])
joined = join(orders, regions, on=["customer_id"], how="left")
paid = where(joined, col("status") == "paid")
by_region = group(paid, by=["region"], revenue=agg.sum(col("qty") * col("unit_price")))
write(by_region, "out.parquet")
