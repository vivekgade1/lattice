import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
paid = orders[orders["status"] == "paid"].copy()
paid["revenue"] = paid["qty"] * paid["unit_price"]
joined = paid.merge(customers, on="customer_id", how="inner")
result = joined.groupby("region", as_index=False).agg(paid_revenue=("revenue", "sum"))
result.to_parquet("out.parquet", index=False)
