import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
paid = orders[orders["status"] == "paid"]
buyers = paid[["customer_id"]].drop_duplicates()
joined = customers.merge(buyers, on="customer_id", how="inner", validate="one_to_one")
result = joined.groupby("tier", as_index=False).agg(n_buyers=("customer_id", "size"))
result.to_parquet("out.parquet", index=False)
