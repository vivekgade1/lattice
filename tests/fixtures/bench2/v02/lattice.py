# lattice: v1
from lattice.ops import agg, coalesce, col, group, join, load, write
from lattice.schemas import Customers2, Orders2

orders = load("orders2.csv", schema=Orders2)
customers = load("customers2.csv", schema=Customers2)
joined = join(orders, customers, on=["customer_id"], how="inner")
per_tier = group(joined, by=["tier"], avg_discount=agg.mean(coalesce(col("discount"), 0.0)))
write(per_tier, "out.parquet")
