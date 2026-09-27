import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"], dtype={"zip": str})
zero_zip = customers[customers["zip"].str.startswith("0")]
result = zero_zip[["customer_id", "name", "zip"]]
result.to_parquet("out.parquet", index=False)
