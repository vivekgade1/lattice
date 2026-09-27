import numpy as np
import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"], dtype={"zip": str})
customers["zip_area"] = np.where(customers["zip"].str.startswith("0"), "northeast", "other")
result = customers.groupby("zip_area", as_index=False).agg(n_customers=("customer_id", "size"))
result.to_parquet("out.parquet", index=False)
