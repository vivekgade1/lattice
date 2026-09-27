# lattice: v1
from lattice.ops import col, load, select, where, write
from lattice.schemas import Products2

products = load("products2.csv", schema=Products2)
office = where(products, col("category") == "office")
result = select(office, cols=["product_id", "product_name", "cost"])
write(result, "out.parquet")
