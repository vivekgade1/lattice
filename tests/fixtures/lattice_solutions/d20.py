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
    load,
    select,
    when,
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
    revenue=agg.sum(col("qty") * col("unit_price")),
    n_paid=agg.count(),
)
with_revenue = join(customers, per_customer, on=["customer_id"], how="left")
bucketed = derive(
    with_revenue,
    tenure_bucket=when(
        fn.date_diff(fn.to_date("2025-01-01", format="%Y-%m-%d"), col("signup_date"), unit="days")
        < 365,
        then="under_1y",
        otherwise=when(
            fn.date_diff(
                fn.to_date("2025-01-01", format="%Y-%m-%d"), col("signup_date"), unit="days"
            )
            < 730,
            then="1_to_2y",
            otherwise="over_2y",
        ),
    ),
    revenue_or_zero=coalesce(col("revenue"), 0.0),
    active=when(is_null(col("n_paid")), then=0, otherwise=1),
)
per_bucket = group(
    bucketed,
    by=["tenure_bucket"],
    n_customers=agg.count(),
    n_active=agg.sum(col("active")),
    total_revenue=agg.sum(col("revenue_or_zero")),
)
rates = derive(
    per_bucket,
    active_share=fn.round(col("n_active") / col("n_customers"), digits=3),
    avg_paid_revenue=fn.round(col("total_revenue") / col("n_customers"), digits=2),
)
result = select(rates, cols=["tenure_bucket", "n_customers", "active_share", "avg_paid_revenue"])
write(result, "out.parquet")
