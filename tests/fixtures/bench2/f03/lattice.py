# lattice: v1
from lattice.ops import agg, col, group, join, load, rename, select, where, write
from lattice.schemas import Orders2, ProductPrices

orders = load("orders2.csv", schema=Orders2)
prices = load("product_prices.csv", schema=ProductPrices)
first = group(prices, by=["product_id"], valid_from=agg.min(col("valid_from")))
original = join(first, prices, on=["product_id", "valid_from"], how="inner")
original_prices = rename(original, price="original_price")
paid = where(orders, col("status") == "paid")
per_product = group(paid, by=["product_id"], paid_qty=agg.sum(col("qty")))
joined = join(per_product, original_prices, on=["product_id"], how="inner")
result = select(joined, cols=["product_id", "paid_qty", "original_price"])
write(result, "out.parquet")
