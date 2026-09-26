# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, when, where, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
joined = join(paid, products, on=["product_id"], how="inner")
flagged = derive(joined, low=when(col("unit_price") < 1.5 * col("cost"), then=1, otherwise=0))
per_category = group(
    flagged, by=["category"], n_paid_orders=agg.count(), n_low_markup=agg.sum(col("low"))
)
shares = derive(
    per_category,
    low_markup_share=fn.round(col("n_low_markup") / col("n_paid_orders"), digits=3),
)
result = select(shares, cols=["category", "n_paid_orders", "n_low_markup", "low_markup_share"])
write(result, "out.parquet")
