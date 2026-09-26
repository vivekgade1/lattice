# lattice: v1
from lattice.ops import agg, coalesce, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Customers, Orders

customers = load("customers.csv", schema=Customers)
orders = load("orders.csv", schema=Orders)
cohorted = derive(customers, signup_year=fn.year(col("signup_date")))
cohorts = group(cohorted, by=["signup_year"], cohort_size=agg.count())
joined = join(orders, cohorted, on=["customer_id"], how="inner")
next_year = where(
    joined,
    (col("status") == "paid") & (fn.year(col("order_date")) == col("signup_year") + 1),
)
per_cohort = group(next_year, by=["signup_year"], next_orders=agg.count())
merged = join(cohorts, per_cohort, on=["signup_year"], how="left")
filled = derive(
    merged,
    next_year_paid_orders=coalesce(col("next_orders"), 0),
    orders_per_customer=fn.round(coalesce(col("next_orders"), 0) / col("cohort_size"), digits=3),
)
result = select(
    filled, cols=["signup_year", "cohort_size", "next_year_paid_orders", "orders_per_customer"]
)
write(result, "out.parquet")
