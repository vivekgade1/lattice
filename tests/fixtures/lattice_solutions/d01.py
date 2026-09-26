# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, where, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
enriched = join(paid, products, on=["product_id"], how="inner")
keyed = derive(enriched, revenue=col("qty") * col("unit_price"), k=1)
by_category = group(keyed, by=["category", "k"], raw_revenue=agg.sum(col("revenue")))
grand = group(by_category, by=["k"], total_revenue=agg.sum(col("raw_revenue")))
combined = join(by_category, grand, on=["k"], how="inner")
shares = derive(
    combined,
    paid_revenue=fn.round(col("raw_revenue"), digits=2),
    revenue_share=fn.round(col("raw_revenue") / col("total_revenue"), digits=4),
)
result = select(shares, cols=["category", "paid_revenue", "revenue_share"])
write(result, "out.parquet")
