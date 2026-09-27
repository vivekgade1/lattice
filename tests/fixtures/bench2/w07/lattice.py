# lattice: v1
from lattice.ops import agg, col, group, join, load, write
from lattice.schemas import OrderFinancials, Orders2

orders = load("orders2.csv", schema=Orders2)
financials = load("order_financials.csv", schema=OrderFinancials)
joined = join(orders, financials, on=["order_id"], how="inner")
per_product = group(
    joined,
    by=["product_id"],
    total_unit_shipping=agg.sum(col("amount_shipping")),
    total_tax_rounded=agg.sum(col("tax_amount_rounded")),
)
write(per_product, "out.parquet")
