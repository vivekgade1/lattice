import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
result = customers[["customer_id", "zip"]]
result.to_parquet("out.parquet", index=False)
