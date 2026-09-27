# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, sort, where, write
from lattice.schemas import OrderFinancials, Orders2

orders = load("orders2.csv", schema=Orders2)
financials = load("order_financials.csv", schema=OrderFinancials)
in_2024 = where(orders, fn.year(col("order_date")) == 2024)
with_month = derive(in_2024, month=fn.month(col("order_date")))
joined = join(with_month, financials, on=["order_id"], how="inner")
per_month = group(joined, by=["month"], fee_usd=agg.sum(col("fee_amount_usd")))
ordered = sort(per_month, by=["month"])
write(ordered, "out.parquet")
