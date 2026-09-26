# lattice: v1
from lattice.ops import agg, col, group, join, load, select, where, write
from lattice.schemas import Customers, Orders, Products

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
products = load("products.csv", schema=Products)
regions = select(customers, cols=["customer_id", "region"])
categories = select(products, cols=["product_id", "category"])
with_region = join(orders, regions, on=["customer_id"], how="left")
with_category = join(with_region, categories, on=["product_id"], how="left")
paid = where(with_category, col("status") == "paid")
revenue = group(paid, by=["region", "category"], revenue=agg.sum(col("qty") * col("unit_price")))
write(revenue, "out.parquet")
