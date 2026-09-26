# lattice: v1
from lattice.ops import load, rename, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
renamed = rename(orders, qty="quantity")
write(renamed, "out.parquet")
