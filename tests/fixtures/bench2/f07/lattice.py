# lattice: v1
from lattice.ops import agg, col, group, join, load, write
from lattice.schemas import ProductPrices, Products2

products = load("products2.csv", schema=Products2)
prices = load("product_prices.csv", schema=ProductPrices)
latest = group(prices, by=["product_id"], valid_from=agg.max(col("valid_from")))
current = join(latest, prices, on=["product_id", "valid_from"], how="inner")
priced = join(products, current, on=["product_id"], how="inner")
per_category = group(priced, by=["category"], avg_current_price=agg.mean(col("price")))
write(per_category, "out.parquet")
