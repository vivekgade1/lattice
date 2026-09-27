# lattice: v1
from lattice.ops import col, fn, join, load, rename, select, where, write
from lattice.schemas import OrderFinancials, Orders2

orders = load("orders2.csv", schema=Orders2)
financials = load("order_financials.csv", schema=OrderFinancials)
in_2024 = where(orders, fn.year(col("order_date")) == 2024)
joined = join(in_2024, financials, on=["order_id"], how="inner")
renamed = rename(joined, amount_net="unit_net")
result = select(renamed, cols=["order_id", "unit_net"])
write(result, "out.parquet")
