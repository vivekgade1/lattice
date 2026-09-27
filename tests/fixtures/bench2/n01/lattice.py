# lattice: v1
from lattice.ops import agg, group, load, write
from lattice.schemas import Customers2

customers = load("customers2.csv", schema=Customers2)
per_region = group(customers, by=["region"], n_customers=agg.count())
write(per_region, "out.parquet")
