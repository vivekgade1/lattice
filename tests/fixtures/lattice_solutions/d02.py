# lattice: v1
from lattice.ops import agg, coalesce, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
paid = where(orders, col("status") == "paid")
per_customer = group(paid, by=["customer_id"], revenue=agg.sum(col("qty") * col("unit_price")))
with_region = join(per_customer, customers, on=["customer_id"], how="inner")
labelled = derive(with_region, region_label=coalesce(col("region"), "unknown"))
by_region = group(
    labelled,
    by=["region_label"],
    n_paying_customers=agg.count(),
    total_revenue=agg.sum(col("revenue")),
)
averaged = derive(
    by_region,
    avg_revenue_per_customer=fn.round(
        col("total_revenue") / col("n_paying_customers"), digits=2
    ),
)
result = select(averaged, cols=["region_label", "n_paying_customers", "avg_revenue_per_customer"])
write(result, "out.parquet")
