import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
result = customers.groupby("region", as_index=False).agg(first_signup=("signup_date", "min"))
result.to_parquet("out.parquet", index=False)
