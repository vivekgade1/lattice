# lattice: v1
from lattice.ops import col, join, load, rename, select, where, write
from lattice.schemas import OrderFinancials, Orders2

orders = load("orders2.csv", schema=Orders2)
financials = load("order_financials.csv", schema=OrderFinancials)
refunded = where(orders, col("status") == "refunded")
joined = join(refunded, financials, on=["order_id"], how="inner")
renamed = rename(joined, refund_amount="refund_total", amount_refund="refund_per_unit")
result = select(renamed, cols=["order_id", "refund_total", "refund_per_unit"])
write(result, "out.parquet")
