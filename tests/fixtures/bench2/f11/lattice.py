# lattice: v1
from lattice.ops import agg, col, derive, group, join, load, select, where, write
from lattice.schemas import Orders2, ProductPrices, Products2

orders = load("orders2.csv", schema=Orders2)
products = load("products2.csv", schema=Products2)
prices = load("product_prices.csv", schema=ProductPrices)
paid = where(orders, col("status") == "paid")
history = select(prices, cols=["product_id", "valid_from"])
candidates = join(paid, history, on=["product_id"], how="inner", fanout="allow")
started = where(candidates, col("valid_from") <= col("order_date"))
effective = group(
    started, by=["order_id", "product_id", "qty"], valid_from=agg.max(col("valid_from"))
)
priced = join(effective, prices, on=["product_id", "valid_from"], how="inner")
with_category = join(priced, products, on=["product_id"], how="inner")
valued = derive(with_category, value=col("qty") * col("price"))
per_category = group(valued, by=["category"], list_value=agg.sum(col("value")))
write(per_category, "out.parquet")
