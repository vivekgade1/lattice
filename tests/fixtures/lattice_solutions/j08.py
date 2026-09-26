# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
regions = select(customers, cols=["customer_id", "region"])
joined = join(orders, regions, on=["customer_id"], how="left")
paid = where(joined, col("status") == "paid")
valued = derive(paid, order_value=col("qty") * col("unit_price"))
by_region = group(valued, by=["region"], mean_value=agg.mean(col("order_value")))
rounded = derive(by_region, avg_order_value=fn.round(col("mean_value"), digits=2))
result = select(rounded, cols=["region", "avg_order_value"])
write(result, "out.parquet")
