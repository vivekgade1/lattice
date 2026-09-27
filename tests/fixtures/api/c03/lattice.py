# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
@endpoint("GET", "/me/stats", auth=Customer)
def my_stats(user: CurrentUser) -> One:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    counted = derive(
        mine,
        spent=when(
            (col("status") == "paid") | (col("status") == "shipped"),
            then=col("total"),
            otherwise=0.0,
        ),
    )
    stats = group(counted, by=[], order_count=agg.count(), total_spent=agg.sum(col("spent")))
    filled = derive(stats, total_spent=coalesce(col("total_spent"), 0.0))
    return filled
