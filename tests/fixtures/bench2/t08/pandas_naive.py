import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
paid = orders[orders["status"] == "paid"]
counts = paid.groupby("customer_id", as_index=False).agg(n=("order_id", "size"))
joined = customers.merge(counts, on="customer_id", how="left")
joined["n_paid_orders"] = joined["n"].fillna(0).astype("int64")
result = joined[["customer_id", "zip", "n_paid_orders"]]
result.to_parquet("out.parquet", index=False)
