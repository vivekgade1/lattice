# lattice: v1
from lattice.ops import agg, col, derive, fn, group, join, load, select, sort, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
valued = derive(paid, revenue=col("qty") * col("unit_price"))
customer_avg = group(valued, by=["customer_id"], avg_revenue=agg.mean(col("revenue")))
compared = join(valued, customer_avg, on=["customer_id"], how="inner")
spikes = where(compared, col("revenue") > 2 * col("avg_revenue"))
ratios = derive(spikes, spike_ratio=fn.round(col("revenue") / col("avg_revenue"), digits=2))
result = select(ratios, cols=["order_id", "customer_id", "spike_ratio"])
ordered = sort(result, by=["spike_ratio", "order_id"], desc=True)
write(ordered, "out.parquet")
