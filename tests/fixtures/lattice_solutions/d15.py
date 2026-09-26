# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, sort, where, write
from lattice.schemas import Customers, Orders

orders = load("orders.csv", schema=Orders)
customers = load("customers.csv", schema=Customers)
paid = where(orders, col("status") == "paid")
pairs = group(paid, by=["customer_id", "product_id"], n_orders=agg.count())
repeats = where(pairs, col("n_orders") >= 2)
per_customer = group(repeats, by=["customer_id"], repeat_products=agg.count())
named = join(per_customer, customers, on=["customer_id"], how="inner")
cleaned = derive(named, name_clean=fn.strip(col("name")))
result = select(cleaned, cols=["customer_id", "name_clean", "repeat_products"])
ordered = sort(result, by=["repeat_products", "customer_id"], desc=True)
write(ordered, "out.parquet")
