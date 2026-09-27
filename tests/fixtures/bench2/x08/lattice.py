# lattice: v1
from lattice.ops import col, join, load, select, where, write
from lattice.schemas import Orders2, Products2

orders = load("orders2.csv", schema=Orders2)
products = load("products2.csv", schema=Products2)
refunded = where(orders, col("status") == "refunded")
named = join(refunded, products, on=["product_id"], how="inner")
electronics = where(named, col("category") == "electronics")
result = select(electronics, cols=["order_id", "product_name", "notes"])
write(result, "out.parquet")
