# lattice: v1
from lattice.ops import agg, col, group, join, load, rename, select, write
from lattice.schemas import ProductPrices, Products2

products = load("products2.csv", schema=Products2)
prices = load("product_prices.csv", schema=ProductPrices)
latest = group(prices, by=["product_id"], valid_from=agg.max(col("valid_from")))
current = join(latest, prices, on=["product_id", "valid_from"], how="inner")
current_prices = rename(current, price="current_price")
priced = join(products, current_prices, on=["product_id"], how="inner")
result = select(priced, cols=["product_id", "product_name", "current_price"])
write(result, "out.parquet")
