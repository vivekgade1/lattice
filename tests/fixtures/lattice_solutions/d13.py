# lattice: v1
from lattice.ops import (
    agg,
    coalesce,
    col,
    derive,
    fn,
    group,
    is_null,
    join,
    load,
    select,
    when,
    where,
    write,
)
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
bucketed = derive(
    paid,
    discount_bucket=when(
        is_null(col("discount")),
        then="none",
        otherwise=when(
            col("discount") < 0.1,
            then="small",
            otherwise=when(col("discount") < 0.2, then="medium", otherwise="large"),
        ),
    ),
    net=col("qty") * col("unit_price") * (1 - coalesce(col("discount"), 0.0)),
    k=1,
)
per_bucket = group(
    bucketed,
    by=["discount_bucket", "k"],
    mean_qty=agg.mean(col("qty")),
    net_total=agg.sum(col("net")),
)
overall = group(bucketed, by=["k"], overall_qty=agg.mean(col("qty")))
compared = join(per_bucket, overall, on=["k"], how="inner")
above = where(compared, col("mean_qty") > col("overall_qty"))
rounded = derive(
    above,
    avg_qty=fn.round(col("mean_qty"), digits=2),
    net_revenue=fn.round(col("net_total"), digits=2),
)
result = select(rounded, cols=["discount_bucket", "avg_qty", "net_revenue"])
write(result, "out.parquet")
