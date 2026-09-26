# lattice: v1
from lattice.ops import col, derive, fn, load, select, write
from lattice.schemas import Customers

customers = load("customers.csv", schema=Customers)
aged = derive(
    customers,
    days_since_signup=fn.date_diff(
        fn.to_date("2025-01-01", format="%Y-%m-%d"), col("signup_date"), unit="days"
    ),
)
result = select(aged, cols=["customer_id", "days_since_signup"])
write(result, "out.parquet")
