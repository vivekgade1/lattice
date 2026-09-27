# lattice: v1
from lattice.ops import load, select, write
from lattice.schemas import Products2

products = load("products2.csv", schema=Products2)
catalog = select(products, cols=["product_id", "product_name", "category", "notes"])
write(catalog, "out.parquet")
