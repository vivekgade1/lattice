# lattice: v1
from lattice.ops import agg, group, load, write
from lattice.schemas import Orders2

orders = load("orders2.csv", schema=Orders2)
per_rating = group(orders, by=["rating"], n_orders=agg.count())
write(per_rating, "out.parquet")
