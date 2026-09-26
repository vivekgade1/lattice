# lattice: v1
from lattice.ops import (
    agg,
    coalesce,
    col,
    derive,
    fn,
    group,
    is_null,
    join,
    limit,
    load,
    sort,
    where,
    write,
)
from lattice.schemas import Customers, Orders

customers = load("customers.csv", schema=Customers)
orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
per_customer = group(
    paid,
    by=["customer_id"],
    net=agg.sum(col("qty") * col("unit_price") * (1 - coalesce(col("discount"), 0.0))),
)
with_net = join(customers, per_customer, on=["customer_id"], how="left")
regioned = where(with_net, ~is_null(col("region")))
segments = group(
    regioned,
    by=["region", "tier"],
    n_customers=agg.count(),
    segment_net_revenue=agg.sum(coalesce(col("net"), 0.0)),
)
averaged = derive(
    segments,
    avg_net_per_customer=fn.round(col("segment_net_revenue") / col("n_customers"), digits=2),
)
ranked = sort(averaged, by=["avg_net_per_customer", "n_customers"], desc=True)
top = limit(ranked, n=5)
write(top, "out.parquet")
