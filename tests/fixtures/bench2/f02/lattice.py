# lattice: v1
from lattice.ops import agg, col, group, join, load, rename, select, where, write
from lattice.schemas import Orders2, ProductPrices

orders = load("orders2.csv", schema=Orders2)
prices = load("product_prices.csv", schema=ProductPrices)
latest = group(prices, by=["product_id"], valid_from=agg.max(col("valid_from")))
current = join(latest, prices, on=["product_id", "valid_from"], how="inner")
current_prices = rename(current, price="current_price")
paid = where(orders, col("status") == "paid")
per_product = group(paid, by=["product_id"], n_paid_orders=agg.count())
joined = join(per_product, current_prices, on=["product_id"], how="inner")
result = select(joined, cols=["product_id", "n_paid_orders", "current_price"])
write(result, "out.parquet")
