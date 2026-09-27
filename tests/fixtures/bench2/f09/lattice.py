# lattice: v1
from lattice.ops import agg, col, fn, group, join, load, where, write
from lattice.schemas import Orders2, Products2

products = load("products2.csv", schema=Products2)
orders = load("orders2.csv", schema=Orders2)
in_2024 = where(orders, fn.year(col("order_date")) == 2024)
ordered_products = group(in_2024, by=["product_id"], n_orders=agg.count())
joined = join(products, ordered_products, on=["product_id"], how="inner")
per_category = group(joined, by=["category"], total_cost=agg.sum(col("cost")))
write(per_category, "out.parquet")
