# lattice: v1
from lattice.ops import agg, col, derive, group, join, load, select, where, write
from lattice.schemas import Customers2, Orders2

orders = load("orders2.csv", schema=Orders2)
customers = load("customers2.csv", schema=Customers2)
paid = where(orders, col("status") == "paid")
with_region = join(paid, customers, on=["customer_id"], how="inner")
per_region = group(with_region, by=["region"], revenue=agg.sum(col("qty") * col("unit_price")))
total = group(per_region, by=[], total_revenue=agg.sum(col("revenue")))
with_total = join(per_region, total, on=[], how="inner")
shares = derive(with_total, revenue_share=col("revenue") / col("total_revenue"))
result = select(shares, cols=["region", "revenue_share"])
write(result, "out.parquet")
