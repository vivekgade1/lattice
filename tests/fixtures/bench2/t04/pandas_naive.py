import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
customers["label"] = customers["name"] + ", " + customers["zip"].astype(str)
result = customers[["customer_id", "label"]]
result.to_parquet("out.parquet", index=False)
