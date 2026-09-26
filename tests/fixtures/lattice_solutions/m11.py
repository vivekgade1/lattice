# lattice: v1
from lattice.ops import agg, col, fn, group, load, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
q4 = where(
    orders,
    (col("order_date") >= fn.to_date("2024-10-01", format="%Y-%m-%d"))
    & (col("order_date") <= fn.to_date("2024-12-31", format="%Y-%m-%d")),
)
counts = group(q4, by=["status"], n_orders=agg.count())
write(counts, "out.parquet")
