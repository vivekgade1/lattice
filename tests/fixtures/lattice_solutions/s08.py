# lattice: v1
from lattice.ops import limit, load, select, sort, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
prices = select(orders, cols=["order_id", "unit_price"])
ranked = sort(prices, by=["unit_price", "order_id"], desc=True)
top10 = limit(ranked, n=10)
write(top10, "out.parquet")
