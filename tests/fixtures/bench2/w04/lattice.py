# lattice: v1
from lattice.ops import limit, load, rename, select, sort, write
from lattice.schemas import OrderFinancials

financials = load("order_financials.csv", schema=OrderFinancials)
renamed = rename(financials, total_amount_rounded="total_rounded")
narrow = select(renamed, cols=["order_id", "total_rounded"])
by_id = sort(narrow, by=["order_id"])
ranked = sort(by_id, by=["total_rounded"], desc=True)
top10 = limit(ranked, n=10)
write(top10, "out.parquet")
