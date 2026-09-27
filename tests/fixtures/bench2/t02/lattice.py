# lattice: v1
from lattice.ops import col, fn, load, select, where, write
from lattice.schemas import Customers2

customers = load("customers2.csv", schema=Customers2)
zero_zip = where(customers, fn.starts_with(col("zip"), prefix="0"))
result = select(zero_zip, cols=["customer_id", "name", "zip"])
write(result, "out.parquet")
