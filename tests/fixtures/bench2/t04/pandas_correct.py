import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"], dtype={"zip": str})
customers["label"] = customers["name"] + ", " + customers["zip"]
result = customers[["customer_id", "label"]]
result.to_parquet("out.parquet", index=False)
