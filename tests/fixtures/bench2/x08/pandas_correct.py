import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
refunded = orders[orders["status"] == "refunded"]
named = refunded.merge(products, on="product_id", how="inner", validate="many_to_one")
electronics = named[named["category"] == "electronics"]
result = electronics[["order_id", "product_name", "notes"]]
result.to_parquet("out.parquet", index=False)
