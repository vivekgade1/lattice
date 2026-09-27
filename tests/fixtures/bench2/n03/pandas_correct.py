import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
joined = orders.merge(customers, on="customer_id", how="inner", validate="many_to_one")
result = joined.groupby(["region", "tier"], as_index=False, dropna=False).agg(
    n_orders=("order_id", "size")
)
result.to_parquet("out.parquet", index=False)
