import pandas as pd

products = pd.read_csv("products2.csv")
office = products[products["category"] == "office"]
result = office[["product_id", "product_name", "cost"]]
result.to_parquet("out.parquet", index=False)
