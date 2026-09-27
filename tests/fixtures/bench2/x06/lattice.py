# lattice: v1
from lattice.ops import col, fn, join, load, select, where, write
from lattice.schemas import Orders2, Products2

orders = load("orders2.csv", schema=Orders2)
products = load("products2.csv", schema=Products2)
paid_2024 = where(orders, (col("status") == "paid") & (fn.year(col("order_date")) == 2024))
named = join(paid_2024, products, on=["product_id"], how="inner")
result = select(named, cols=["order_id", "product_name", "notes"])
write(result, "out.parquet")
