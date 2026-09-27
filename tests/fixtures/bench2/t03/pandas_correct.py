import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"], dtype={"zip": str})
customers["zero_zip"] = customers["zip"].str.startswith("0").astype("int64")
result = customers.groupby("tier", as_index=False).agg(
    n_customers=("customer_id", "size"), n_zero_zip=("zero_zip", "sum")
)
result.to_parquet("out.parquet", index=False)
