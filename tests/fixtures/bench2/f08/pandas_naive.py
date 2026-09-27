import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
paid = orders[orders["status"] == "paid"]
joined = customers.merge(paid, on="customer_id", how="inner")
result = joined.groupby("tier", as_index=False).agg(n_buyers=("customer_id", "count"))
result.to_parquet("out.parquet", index=False)
