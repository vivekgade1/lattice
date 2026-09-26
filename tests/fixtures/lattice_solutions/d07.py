# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, limit, load, select, sort, where, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
joined = join(paid, products, on=["product_id"], how="inner")
per_product = group(
    joined,
    by=["product_id", "product_name"],
    n_orders=agg.count(),
    margin=agg.sum(col("qty") * (col("unit_price") - col("cost"))),
    revenue=agg.sum(col("qty") * col("unit_price")),
)
busy = where(per_product, col("n_orders") >= 10)
ranked = sort(busy, by=["margin"], desc=True)
top = limit(ranked, n=5)
rounded = derive(
    top,
    paid_margin=fn.round(col("margin"), digits=2),
    margin_pct=fn.round(100 * col("margin") / col("revenue"), digits=1),
)
result = select(rounded, cols=["product_id", "product_name", "paid_margin", "margin_pct"])
write(result, "out.parquet")
