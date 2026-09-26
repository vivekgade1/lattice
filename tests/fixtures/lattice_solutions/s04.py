# lattice: v1
from lattice.ops import col, load, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
bulk = where(orders, col("qty") >= 5)
write(bulk, "out.parquet")
