# lattice: v1
from lattice.ops import agg, col, group, join, limit, load, select, sort, where, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
joined = join(orders, products, on=["product_id"], how="left")
paid = where(joined, col("status") == "paid")
margins = group(
    paid,
    by=["product_id", "product_name"],
    margin=agg.sum((col("unit_price") - col("cost")) * col("qty")),
)
ranked = sort(margins, by=["margin", "product_id"], desc=True)
top5 = limit(ranked, n=5)
result = select(top5, cols=["product_name", "margin"])
write(result, "out.parquet")
