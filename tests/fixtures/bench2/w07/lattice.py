# lattice: v1
from lattice.ops import agg, col, group, join, load, write
from lattice.schemas import OrderFinancials, Orders2

orders = load("orders2.csv", schema=Orders2)
financials = load("order_financials.csv", schema=OrderFinancials)
joined = join(orders, financials, on=["order_id"], how="inner")
per_product = group(
    joined,
    by=["product_id"],
    total_shipping=agg.sum(col("shipping_amount")),
    total_tax=agg.sum(col("tax_amount_usd")),
)
write(per_product, "out.parquet")
