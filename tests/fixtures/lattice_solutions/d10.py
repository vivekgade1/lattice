# lattice: v1
from lattice.ops import agg, col, derive, fn, group, load, select, sort, when, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
dated = derive(
    paid,
    order_month=fn.month(col("order_date")),
    order_year=fn.year(col("order_date")),
    revenue=col("qty") * col("unit_price"),
)
monthly = group(
    dated,
    by=["order_month"],
    raw_2023=agg.sum(when(col("order_year") == 2023, then=col("revenue"), otherwise=0.0)),
    raw_2024=agg.sum(when(col("order_year") == 2024, then=col("revenue"), otherwise=0.0)),
)
growth = derive(
    monthly,
    revenue_2023=fn.round(col("raw_2023"), digits=2),
    revenue_2024=fn.round(col("raw_2024"), digits=2),
    growth_pct=fn.round(100 * (col("raw_2024") - col("raw_2023")) / col("raw_2023"), digits=1),
)
result = select(growth, cols=["order_month", "revenue_2023", "revenue_2024", "growth_pct"])
ordered = sort(result, by=["order_month"])
write(ordered, "out.parquet")
