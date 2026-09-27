import pandas as pd

products = pd.read_csv("products2.csv")
pricey = products[products["cost"] > 40]
result = pricey[["product_id", "product_name", "notes"]]
result.to_parquet("out.parquet", index=False)
