import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
joined = orders.merge(customers, on="customer_id", how="inner")
result = joined.groupby("region", as_index=False).agg(avg_qty=("qty", "mean"))
result.to_parquet("out.parquet", index=False)
