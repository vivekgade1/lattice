# lattice: v1
from lattice.ops import col, derive, fn, load, select, write
from lattice.schemas import Customers

customers = load("customers.csv", schema=Customers)
stripped = derive(customers, clean_name=fn.strip(col("name")))
measured = derive(
    stripped, name_upper=fn.upper(col("clean_name")), name_len=fn.len(col("clean_name"))
)
result = select(measured, cols=["customer_id", "name_upper", "name_len"])
write(result, "out.parquet")
