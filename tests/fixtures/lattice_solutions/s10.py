# lattice: v1
from lattice.ops import col, derive, fn, load, select, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
with_year = derive(orders, order_year=fn.year(col("order_date")))
result = select(with_year, cols=["order_id", "order_year"])
write(result, "out.parquet")
