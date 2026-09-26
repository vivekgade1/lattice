# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, when, where, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
joined = join(orders, products, on=["product_id"], how="inner")
flagged = derive(
    joined,
    order_year=fn.year(col("order_date")),
    lost=when(
        (col("status") == "cancelled") | (col("status") == "refunded"), then=1, otherwise=0
    ),
)
per_year = group(
    flagged, by=["category", "order_year"], n_orders=agg.count(), n_lost=agg.sum(col("lost"))
)
overall = group(
    per_year,
    by=["category"],
    total_orders=agg.sum(col("n_orders")),
    total_lost=agg.sum(col("n_lost")),
)
compared = join(per_year, overall, on=["category"], how="inner")
worse = where(compared, col("n_lost") / col("n_orders") > col("total_lost") / col("total_orders"))
rates = derive(
    worse,
    year_loss_rate=fn.round(col("n_lost") / col("n_orders"), digits=3),
    overall_loss_rate=fn.round(col("total_lost") / col("total_orders"), digits=3),
)
result = select(rates, cols=["category", "order_year", "year_loss_rate", "overall_loss_rate"])
write(result, "out.parquet")
