# lattice: v1
from lattice.ops import agg, col, derive, group, join, load, rename, where, write
from lattice.schemas import Orders2, ProductPrices, Products2

orders = load("orders2.csv", schema=Orders2)
products = load("products2.csv", schema=Products2)
prices = load("product_prices.csv", schema=ProductPrices)
latest = group(prices, by=["product_id"], valid_from=agg.max(col("valid_from")))
current = join(latest, prices, on=["product_id", "valid_from"], how="inner")
current_prices = rename(current, price="current_price")
paid = where(orders, col("status") == "paid")
priced = join(paid, current_prices, on=["product_id"], how="inner")
with_category = join(priced, products, on=["product_id"], how="inner")
valued = derive(with_category, value=col("qty") * col("current_price"))
per_category = group(valued, by=["category"], value_at_current_price=agg.sum(col("value")))
write(per_category, "out.parquet")
