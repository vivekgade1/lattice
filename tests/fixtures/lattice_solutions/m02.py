# lattice: v1
from lattice.ops import agg, col, group, load, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
per_product = group(
    paid,
    by=["product_id"],
    total_revenue=agg.sum(col("qty") * col("unit_price")),
    mean_qty=agg.mean(col("qty")),
)
big_sellers = where(per_product, col("total_revenue") > 500)
write(big_sellers, "out.parquet")
