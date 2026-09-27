import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
contacts = pd.read_csv("customer_contacts.csv")
merged = customers.merge(contacts, on="customer_id", how="left")
result = merged.groupby("tier", as_index=False).agg(
    n_customers=("customer_id", "count"), n_contacts=("contact_id", "count")
)
result.to_parquet("out.parquet", index=False)
