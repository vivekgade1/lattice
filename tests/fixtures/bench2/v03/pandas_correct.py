import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
orders["rating"] = orders["rating"].fillna(0.0)
result = orders.groupby("customer_id", as_index=False).agg(lowest_rating=("rating", "min"))
result.to_parquet("out.parquet", index=False)
