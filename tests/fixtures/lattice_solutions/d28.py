# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, when, where, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
joined = join(paid, products, on=["product_id"], how="inner")
per_category = group(
    joined,
    by=["category"],
    raw_2023=agg.sum(
        when(
            fn.year(col("order_date")) == 2023,
            then=col("qty") * col("unit_price"),
            otherwise=0.0,
        )
    ),
    raw_2024=agg.sum(
        when(
            fn.year(col("order_date")) == 2024,
            then=col("qty") * col("unit_price"),
            otherwise=0.0,
        )
    ),
)
labelled = derive(
    per_category,
    revenue_2023=fn.round(col("raw_2023"), digits=2),
    revenue_2024=fn.round(col("raw_2024"), digits=2),
    trend=when(
        col("raw_2024") > 1.1 * col("raw_2023"),
        then="growing",
        otherwise=when(
            col("raw_2024") < 0.9 * col("raw_2023"), then="shrinking", otherwise="flat"
        ),
    ),
)
result = select(labelled, cols=["category", "revenue_2023", "revenue_2024", "trend"])
write(result, "out.parquet")
