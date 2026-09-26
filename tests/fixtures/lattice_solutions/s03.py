# lattice: v1
from lattice.ops import col, load, where, write
from lattice.schemas import Customers

customers = load("customers.csv", schema=Customers)
west = where(customers, col("region") == "west")
write(west, "out.parquet")
