# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, when, where, write
from lattice.schemas import Orders, Products

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
paid = where(orders, col("status") == "paid")
joined = join(paid, products, on=["product_id"], how="inner")
lined = derive(
    joined,
    product_line=when(
        fn.contains(fn.lower(col("product_name")), sub="chair")
        | fn.contains(fn.lower(col("product_name")), sub="desk")
        | fn.contains(fn.lower(col("product_name")), sub="shelf")
        | fn.contains(fn.lower(col("product_name")), sub="lamp"),
        then="furniture",
        otherwise="gear",
    ),
    markup=col("unit_price") / col("cost"),
)
per_line = group(
    lined,
    by=["product_line"],
    n_paid_orders=agg.count(),
    raw_revenue=agg.sum(col("qty") * col("unit_price")),
    mean_markup=agg.mean(col("markup")),
)
rounded = derive(
    per_line,
    paid_revenue=fn.round(col("raw_revenue"), digits=2),
    avg_markup=fn.round(col("mean_markup"), digits=3),
)
result = select(rounded, cols=["product_line", "n_paid_orders", "paid_revenue", "avg_markup"])
write(result, "out.parquet")
