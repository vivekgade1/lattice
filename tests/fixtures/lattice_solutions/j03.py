# lattice: v1
from lattice.ops import agg, col, group, join, load, select, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
categories = select(products, cols=["product_id", "category"])
joined = join(orders, categories, on=["product_id"], how="left")
by_category = group(joined, by=["category"], revenue=agg.sum(col("qty") * col("unit_price")))
write(by_category, "out.parquet")
