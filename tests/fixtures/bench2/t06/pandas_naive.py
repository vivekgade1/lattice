import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
result = customers.groupby("tier", as_index=False).agg(min_zip=("zip", "min"))
result.to_parquet("out.parquet", index=False)
