# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, sort, when, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
dated = derive(
    paid,
    order_year=fn.year(col("order_date")),
    quarter=when(
        fn.month(col("order_date")) <= 3,
        then=1,
        otherwise=when(
            fn.month(col("order_date")) <= 6,
            then=2,
            otherwise=when(fn.month(col("order_date")) <= 9, then=3, otherwise=4),
        ),
    ),
    line_revenue=col("qty") * col("unit_price"),
)
cells = group(dated, by=["order_year", "quarter"], quarter_revenue=agg.sum(col("line_revenue")))
totals = group(cells, by=["order_year"], year_total=agg.sum(col("quarter_revenue")))
combined = join(cells, totals, on=["order_year"], how="inner")
shares = derive(
    combined,
    revenue=fn.round(col("quarter_revenue"), digits=2),
    year_share=fn.round(col("quarter_revenue") / col("year_total"), digits=4),
)
result = select(shares, cols=["order_year", "quarter", "revenue", "year_share"])
ordered = sort(result, by=["order_year", "quarter"])
write(ordered, "out.parquet")
