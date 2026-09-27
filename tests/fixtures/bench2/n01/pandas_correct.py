import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
result = customers.groupby("region", as_index=False, dropna=False).agg(
    n_customers=("customer_id", "size")
)
result.to_parquet("out.parquet", index=False)
