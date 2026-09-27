import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
result = orders.groupby("rating", as_index=False, dropna=False).agg(
    n_orders=("order_id", "size")
)
result.to_parquet("out.parquet", index=False)
