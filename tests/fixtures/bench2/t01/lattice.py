# lattice: v1
from lattice.ops import load, select, write
from lattice.schemas import Customers2

customers = load("customers2.csv", schema=Customers2)
result = select(customers, cols=["customer_id", "zip"])
write(result, "out.parquet")
