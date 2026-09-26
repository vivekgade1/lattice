# lattice: v1
from lattice.ops import agg, group, load, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
counts = group(orders, by=["status"], n_orders=agg.count())
write(counts, "out.parquet")
