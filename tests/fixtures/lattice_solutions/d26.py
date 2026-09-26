# lattice: v1
from lattice.ops import agg, coalesce, col, derive, fn, group, join, load, when, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
paid = where(orders, col("status") == "paid")
joined = join(paid, customers, on=["customer_id"], how="inner")
pointed = derive(
    joined,
    points=fn.to_int(fn.floor(col("qty") * col("unit_price") / 10))
    * when(col("tier") == "gold", then=2, otherwise=1),
    region_label=coalesce(col("region"), "unknown"),
)
per_customer = group(
    pointed, by=["customer_id", "region_label"], customer_points=agg.sum(col("points"))
)
loyal = where(per_customer, col("customer_points") >= 400)
per_region = group(
    loyal,
    by=["region_label"],
    n_loyal_customers=agg.count(),
    total_points=agg.sum(col("customer_points")),
)
write(per_region, "out.parquet")
