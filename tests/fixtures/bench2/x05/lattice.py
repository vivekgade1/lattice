# lattice: v1
from lattice.ops import col, load, select, where, write
from lattice.schemas import Products2

products = load("products2.csv", schema=Products2)
pricey = where(products, col("cost") > 40.0)
result = select(pricey, cols=["product_id", "product_name", "notes"])
write(result, "out.parquet")
