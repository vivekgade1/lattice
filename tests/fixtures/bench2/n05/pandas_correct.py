import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
paid = orders[orders["status"] == "paid"]
joined = paid.merge(customers, on="customer_id", how="inner", validate="many_to_one")
result = joined.groupby("region", as_index=False, dropna=False).agg(
    n_buyers=("customer_id", "nunique")
)
result.to_parquet("out.parquet", index=False)
