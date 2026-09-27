# lattice: v1
from lattice.ops import agg, coalesce, col, derive, fn, group, load, sort, where, write
from lattice.schemas import Orders2

orders = load("orders2.csv", schema=Orders2)
in_2024 = where(orders, fn.year(col("order_date")) == 2024)
with_month = derive(in_2024, month=fn.month(col("order_date")))
per_month = group(with_month, by=["month"], avg_rating=agg.mean(coalesce(col("rating"), 0.0)))
ordered = sort(per_month, by=["month"])
write(ordered, "out.parquet")
