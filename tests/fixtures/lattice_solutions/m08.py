# lattice: v1
from lattice.ops import (
    Float,
    Table,
    agg,
    col,
    derive,
    group,
    limit,
    load,
    select,
    sort,
    step,
    where,
    write,
)
from lattice.schemas import Orders


@step
def big_customers(orders: Table[Orders], min_revenue: Float) -> Table:
    paid = where(orders, col("status") == "paid")
    totals = group(paid, by=["customer_id"], total_revenue=agg.sum(col("qty") * col("unit_price")))
    big = where(totals, col("total_revenue") >= min_revenue)
    return big


orders = load("orders.csv", schema=Orders)
qualifying = big_customers(orders, min_revenue=1000)
keyed = derive(qualifying, neg_customer_id=-col("customer_id"))
ranked = sort(keyed, by=["total_revenue", "neg_customer_id"], desc=True)
top5 = limit(ranked, n=5)
result = select(top5, cols=["customer_id", "total_revenue"])
write(result, "out.parquet")
