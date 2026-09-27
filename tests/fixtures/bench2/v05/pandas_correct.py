import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
orders["rating"] = orders["rating"].fillna(0.0)
result = orders.groupby("status", as_index=False).agg(
    n_orders=("order_id", "size"), avg_rating=("rating", "mean")
)
result.to_parquet("out.parquet", index=False)
