import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
orders["rating"] = orders["rating"].fillna(0.0)
result = orders.groupby("product_id", as_index=False).agg(avg_rating=("rating", "mean"))
result.to_parquet("out.parquet", index=False)
