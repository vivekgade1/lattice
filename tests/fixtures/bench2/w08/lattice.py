# lattice: v1
from lattice.ops import agg, col, group, join, load, write
from lattice.schemas import OrderFinancials, Orders2

orders = load("orders2.csv", schema=Orders2)
financials = load("order_financials.csv", schema=OrderFinancials)
joined = join(orders, financials, on=["order_id"], how="inner")
per_status = group(joined, by=["status"], max_unit_discount=agg.max(col("amount_discount")))
write(per_status, "out.parquet")
