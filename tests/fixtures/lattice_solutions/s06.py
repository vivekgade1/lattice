# lattice: v1
from lattice.ops import load, select, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
slim = select(orders, cols=["order_id", "customer_id", "status"])
write(slim, "out.parquet")
