# lattice: v1
from lattice.ops import agg, coalesce, col, derive, group, join, load, select, write
from lattice.schemas import Orders2, Products2

products = load("products2.csv", schema=Products2)
orders = load("orders2.csv", schema=Orders2)
totals = group(orders, by=["product_id"], ordered_qty=agg.sum(col("qty")))
joined = join(products, totals, on=["product_id"], how="left")
filled = derive(joined, total_qty=coalesce(col("ordered_qty"), 0))
result = select(filled, cols=["product_id", "notes", "total_qty"])
write(result, "out.parquet")
