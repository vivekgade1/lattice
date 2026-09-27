# lattice: v1
from lattice.ops import agg, col, group, load, write
from lattice.schemas import Customers2

customers = load("customers2.csv", schema=Customers2)
per_region = group(customers, by=["region"], first_signup=agg.min(col("signup_date")))
write(per_region, "out.parquet")
