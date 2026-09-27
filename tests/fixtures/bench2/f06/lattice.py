# lattice: v1
from lattice.ops import agg, col, derive, group, join, load, select, where, write
from lattice.schemas import Orders2, ProductPrices

orders = load("orders2.csv", schema=Orders2)
prices = load("product_prices.csv", schema=ProductPrices)
paid = where(orders, col("status") == "paid")
history = select(prices, cols=["product_id", "valid_from"])
candidates = join(paid, history, on=["product_id"], how="inner", fanout="allow")
started = where(candidates, col("valid_from") <= col("order_date"))
effective = group(
    started, by=["order_id", "product_id", "qty"], valid_from=agg.max(col("valid_from"))
)
priced = join(effective, prices, on=["product_id", "valid_from"], how="inner")
revenue = derive(priced, list_revenue=col("qty") * col("price"))
result = select(revenue, cols=["order_id", "list_revenue"])
write(result, "out.parquet")
