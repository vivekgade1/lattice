# lattice: v1
from lattice.ops import col, load, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
write(paid, "out.parquet")
