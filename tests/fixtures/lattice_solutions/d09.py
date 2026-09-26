# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
paid = where(orders, col("status") == "paid")
first = group(paid, by=["customer_id"], first_paid=agg.min(col("order_date")))
joined = join(first, customers, on=["customer_id"], how="inner")
measured = derive(
    joined,
    days_to_first=fn.date_diff(col("first_paid"), col("signup_date"), unit="days"),
    signup_year=fn.year(col("signup_date")),
)
kept = where(measured, col("days_to_first") >= 0)
by_year = group(
    kept, by=["signup_year"], n_customers=agg.count(), mean_days=agg.mean(col("days_to_first"))
)
rounded = derive(by_year, avg_days_to_first_order=fn.round(col("mean_days"), digits=1))
result = select(rounded, cols=["signup_year", "n_customers", "avg_days_to_first_order"])
write(result, "out.parquet")
