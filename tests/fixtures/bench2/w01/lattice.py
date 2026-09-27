# lattice: v1
from lattice.ops import agg, col, group, join, load, where, write
from lattice.schemas import Customers2, OrderFinancials, Orders2

orders = load("orders2.csv", schema=Orders2)
customers = load("customers2.csv", schema=Customers2)
financials = load("order_financials.csv", schema=OrderFinancials)
paid = where(orders, col("status") == "paid")
with_fin = join(paid, financials, on=["order_id"], how="inner")
with_tier = join(with_fin, customers, on=["customer_id"], how="inner")
per_tier = group(
    with_tier, by=["tier"], total_gross_rounded=agg.sum(col("gross_amount_rounded"))
)
write(per_tier, "out.parquet")
