# lattice: v1
from lattice.ops import agg, col, derive, fn, group, load, when, write
from lattice.schemas import Customers2

customers = load("customers2.csv", schema=Customers2)
with_area = derive(
    customers,
    zip_area=when(fn.starts_with(col("zip"), prefix="0"), then="northeast", otherwise="other"),
)
per_area = group(with_area, by=["zip_area"], n_customers=agg.count())
write(per_area, "out.parquet")
