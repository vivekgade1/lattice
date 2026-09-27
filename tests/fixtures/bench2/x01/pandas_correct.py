import pandas as pd

products = pd.read_csv("products2.csv")
result = products[["product_id", "product_name", "category", "notes"]]
result.to_parquet("out.parquet", index=False)
