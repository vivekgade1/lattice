# lattice: v1
from lattice.ops import agg, col, group, join, load, select, where, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
joined = join(paid, products, on=["product_id"], how="inner")
per_category = group(joined, by=["customer_id", "category"], category_qty=agg.sum(col("qty")))
best = group(per_category, by=["customer_id"], best_qty=agg.max(col("category_qty")))
compared = join(per_category, best, on=["customer_id"], how="inner")
favourites = where(compared, col("category_qty") == col("best_qty"))
result = select(favourites, cols=["customer_id", "category", "category_qty"])
write(result, "out.parquet")
