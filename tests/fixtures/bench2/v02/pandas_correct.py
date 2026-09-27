import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
joined = orders.merge(customers, on="customer_id", how="inner", validate="many_to_one")
joined["discount"] = joined["discount"].fillna(0.0)
result = joined.groupby("tier", as_index=False).agg(avg_discount=("discount", "mean"))
result.to_parquet("out.parquet", index=False)
