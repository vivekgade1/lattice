# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, when, where, write
from lattice.schemas import Customers, Orders, Products

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
with_customer = join(paid, customers, on=["customer_id"], how="inner")
with_product = join(with_customer, products, on=["product_id"], how="inner")
per_category = group(
    with_product,
    by=["category"],
    gold_qty=agg.sum(when(col("tier") == "gold", then=col("qty"), otherwise=0)),
    silver_qty=agg.sum(when(col("tier") == "silver", then=col("qty"), otherwise=0)),
    bronze_qty=agg.sum(when(col("tier") == "bronze", then=col("qty"), otherwise=0)),
    total_qty=agg.sum(col("qty")),
)
shares = derive(
    per_category, gold_qty_share=fn.round(col("gold_qty") / col("total_qty"), digits=3)
)
result = select(
    shares, cols=["category", "gold_qty", "silver_qty", "bronze_qty", "gold_qty_share"]
)
write(result, "out.parquet")
