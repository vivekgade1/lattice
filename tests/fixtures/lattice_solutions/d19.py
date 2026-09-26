# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, when, where, write
from lattice.schemas import Customers, Orders, Products

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
joined = join(paid, customers, on=["customer_id"], how="inner")
per_product = group(
    joined,
    by=["product_id"],
    gold_qty=agg.sum(when(col("tier") == "gold", then=col("qty"), otherwise=0)),
    total_qty=agg.sum(col("qty")),
)
shares = derive(per_product, gold_share=col("gold_qty") / col("total_qty"))
strong = where(shares, col("gold_share") >= 0.4)
named = join(strong, products, on=["product_id"], how="inner")
rounded = derive(named, gold_qty_share=fn.round(col("gold_share"), digits=3))
result = select(rounded, cols=["product_id", "product_name", "gold_qty_share"])
write(result, "out.parquet")
