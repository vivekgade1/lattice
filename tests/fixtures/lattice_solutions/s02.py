# lattice: v1
from lattice.ops import col, derive, load, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
with_revenue = derive(orders, revenue=col("qty") * col("unit_price"))
write(with_revenue, "out.parquet")
