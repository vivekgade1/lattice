# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
per_product = group(paid, by=["product_id"], paid_qty=agg.sum(col("qty")))
named = join(per_product, products, on=["product_id"], how="inner")
category_avg = group(named, by=["category"], avg_qty=agg.mean(col("paid_qty")))
compared = join(named, category_avg, on=["category"], how="inner")
above = where(compared, col("paid_qty") > col("avg_qty"))
ratios = derive(above, qty_vs_category_avg=fn.round(col("paid_qty") / col("avg_qty"), digits=2))
result = select(ratios, cols=["category", "product_id", "paid_qty", "qty_vs_category_avg"])
write(result, "out.parquet")
