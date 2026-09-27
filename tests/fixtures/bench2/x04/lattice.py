# lattice: v1
from lattice.ops import agg, col, group, join, limit, load, select, sort, where, write
from lattice.schemas import Orders2, Products2

orders = load("orders2.csv", schema=Orders2)
products = load("products2.csv", schema=Products2)
paid = where(orders, col("status") == "paid")
per_product = group(paid, by=["product_id"], paid_revenue=agg.sum(col("qty") * col("unit_price")))
named = join(per_product, products, on=["product_id"], how="inner")
by_id = sort(named, by=["product_id"])
ranked = sort(by_id, by=["paid_revenue"], desc=True)
top5 = limit(ranked, n=5)
result = select(top5, cols=["product_id", "product_name", "notes", "paid_revenue"])
write(result, "out.parquet")
