# lattice: v1
from lattice.ops import agg, col, group, join, load, select, where, write
from lattice.schemas import Orders2, Products2

orders = load("orders2.csv", schema=Orders2)
products = load("products2.csv", schema=Products2)
paid = where(orders, col("status") == "paid")
per_product = group(
    paid,
    by=["product_id"],
    n_paid_orders=agg.count(),
    paid_revenue=agg.sum(col("qty") * col("unit_price")),
)
named = join(per_product, products, on=["product_id"], how="inner")
result = select(named, cols=["product_name", "n_paid_orders", "paid_revenue"])
write(result, "out.parquet")
