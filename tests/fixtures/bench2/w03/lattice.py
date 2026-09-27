# lattice: v1
from lattice.ops import agg, col, group, join, load, where, write
from lattice.schemas import OrderFinancials, Orders2, Products2

orders = load("orders2.csv", schema=Orders2)
products = load("products2.csv", schema=Products2)
financials = load("order_financials.csv", schema=OrderFinancials)
paid = where(orders, col("status") == "paid")
with_fin = join(paid, financials, on=["order_id"], how="inner")
with_category = join(with_fin, products, on=["product_id"], how="inner")
per_category = group(with_category, by=["category"], avg_margin=agg.mean(col("margin_amount")))
write(per_category, "out.parquet")
