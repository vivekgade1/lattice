import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
zero_zip = customers[customers["zip"].astype(str).str.startswith("0")]
result = zero_zip[["customer_id", "name", "zip"]]
result.to_parquet("out.parquet", index=False)
