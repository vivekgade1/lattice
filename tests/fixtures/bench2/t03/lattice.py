# lattice: v1
from lattice.ops import agg, col, derive, fn, group, load, when, write
from lattice.schemas import Customers2

customers = load("customers2.csv", schema=Customers2)
flagged = derive(
    customers, zero_zip=when(fn.starts_with(col("zip"), prefix="0"), then=1, otherwise=0)
)
per_tier = group(
    flagged, by=["tier"], n_customers=agg.count(), n_zero_zip=agg.sum(col("zero_zip"))
)
write(per_tier, "out.parquet")
