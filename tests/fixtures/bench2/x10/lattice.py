# lattice: v1
from lattice.ops import agg, group, join, load, select, write
from lattice.schemas import Orders2, Products2

orders = load("orders2.csv", schema=Orders2)
products = load("products2.csv", schema=Products2)
pairs = group(orders, by=["product_id", "customer_id"], n_orders=agg.count())
per_product = group(pairs, by=["product_id"], n_customers=agg.count())
named = join(per_product, products, on=["product_id"], how="inner")
result = select(named, cols=["product_name", "n_customers"])
write(result, "out.parquet")
