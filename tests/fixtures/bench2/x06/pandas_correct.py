import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
paid_2024 = orders[(orders["status"] == "paid") & (orders["order_date"].dt.year == 2024)]
named = paid_2024.merge(products, on="product_id", how="inner", validate="many_to_one")
result = named[["order_id", "product_name", "notes"]]
result.to_parquet("out.parquet", index=False)
