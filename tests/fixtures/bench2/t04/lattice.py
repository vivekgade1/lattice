# lattice: v1
from lattice.ops import col, derive, fn, load, select, write
from lattice.schemas import Customers2

customers = load("customers2.csv", schema=Customers2)
labelled = derive(customers, label=fn.concat(fn.concat(col("name"), ", "), col("zip")))
result = select(labelled, cols=["customer_id", "label"])
write(result, "out.parquet")
