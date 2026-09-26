# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
paid = where(orders, col("status") == "paid")
per_customer = group(paid, by=["customer_id"], revenue=agg.sum(col("qty") * col("unit_price")))
with_region = join(per_customer, customers, on=["customer_id"], how="inner")
region_avg = group(with_region, by=["region"], region_avg=agg.mean(col("revenue")))
compared = join(with_region, region_avg, on=["region"], how="inner")
above = where(compared, col("revenue") > col("region_avg"))
rounded = derive(
    above,
    paid_revenue=fn.round(col("revenue"), digits=2),
    region_avg_revenue=fn.round(col("region_avg"), digits=2),
)
result = select(rounded, cols=["customer_id", "region", "paid_revenue", "region_avg_revenue"])
write(result, "out.parquet")
