# lattice: v1
from lattice.ops import agg, col, derive, group, load, when, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
sized = derive(
    orders,
    size=when(
        col("qty") >= 10,
        then="large",
        otherwise=when(col("qty") >= 3, then="medium", otherwise="small"),
    ),
)
counts = group(sized, by=["size"], n_orders=agg.count())
write(counts, "out.parquet")
