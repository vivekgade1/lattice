# lattice: v1
from lattice.ops import agg, col, derive, fn, group, load, write
from lattice.schemas import Customers

customers = load("customers.csv", schema=Customers)
with_year = derive(customers, signup_year=fn.year(col("signup_date")))
counts = group(with_year, by=["tier", "signup_year"], n_customers=agg.count())
write(counts, "out.parquet")
