# lattice: v1
from lattice.ops import Str, Table, agg, col, group, load, step, where, write
from lattice.schemas import Orders


@step
def revenue_by_customer(orders: Table[Orders], wanted: Str) -> Table:
    kept = where(orders, col("status") == wanted)
    totals = group(kept, by=["customer_id"], total_revenue=agg.sum(col("qty") * col("unit_price")))
    return totals


orders = load("orders.csv", schema=Orders)
paid_revenue = revenue_by_customer(orders, wanted="paid")
write(paid_revenue, "out.parquet")
