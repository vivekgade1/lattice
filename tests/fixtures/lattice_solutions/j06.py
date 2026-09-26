# lattice: v1
from lattice.ops import agg, group, join, load, select, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
tiers = select(customers, cols=["customer_id", "tier"])
joined = join(orders, tiers, on=["customer_id"], how="left")
by_tier = group(joined, by=["tier"], n_orders=agg.count())
write(by_tier, "out.parquet")
