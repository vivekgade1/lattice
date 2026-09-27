# lattice: v1
from lattice.ops import agg, col, group, load, write
from lattice.schemas import Customers2

customers = load("customers2.csv", schema=Customers2)
per_tier = group(customers, by=["tier"], min_zip=agg.min(col("zip")))
write(per_tier, "out.parquet")
