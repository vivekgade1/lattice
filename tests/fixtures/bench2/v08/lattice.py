# lattice: v1
from lattice.ops import agg, coalesce, col, fn, group, join, load, where, write
from lattice.schemas import Orders2, Products2

orders = load("orders2.csv", schema=Orders2)
products = load("products2.csv", schema=Products2)
in_2024 = where(orders, fn.year(col("order_date")) == 2024)
joined = join(in_2024, products, on=["product_id"], how="inner")
per_category = group(
    joined, by=["category"], avg_discount_2024=agg.mean(coalesce(col("discount"), 0.0))
)
write(per_category, "out.parquet")
