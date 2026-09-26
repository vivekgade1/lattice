# lattice: v1
from lattice.ops import agg, col, derive, fn, group, load, sort, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
paid_2024 = where(orders, (col("status") == "paid") & (fn.year(col("order_date")) == 2024))
with_month = derive(paid_2024, month=fn.month(col("order_date")))
monthly = group(with_month, by=["month"], revenue=agg.sum(col("qty") * col("unit_price")))
ordered = sort(monthly, by=["month"])
write(ordered, "out.parquet")
