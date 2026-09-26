# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Customers, Orders, Products

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
with_customer = join(paid, customers, on=["customer_id"], how="inner")
with_product = join(with_customer, products, on=["product_id"], how="inner")
cells = group(
    with_product, by=["region", "category"], raw=agg.sum(col("qty") * col("unit_price"))
)
totals = group(cells, by=["region"], region_total=agg.sum(col("raw")))
combined = join(cells, totals, on=["region"], how="inner")
shares = derive(
    combined,
    paid_revenue=fn.round(col("raw"), digits=2),
    region_share=fn.round(col("raw") / col("region_total"), digits=4),
)
result = select(shares, cols=["region", "category", "paid_revenue", "region_share"])
write(result, "out.parquet")
