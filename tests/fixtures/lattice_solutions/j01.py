# lattice: v1
from lattice.ops import join, load, select, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
regions = select(customers, cols=["customer_id", "region"])
joined = join(orders, regions, on=["customer_id"], how="left")
result = select(joined, cols=["order_id", "region"])
write(result, "out.parquet")
