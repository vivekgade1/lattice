import pandas as pd

customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
contacts = pd.read_csv("customer_contacts.csv")
contact_counts = contacts.groupby("customer_id", as_index=False).agg(n=("contact_id", "size"))
joined = customers.merge(contact_counts, on="customer_id", how="left", validate="one_to_one")
joined["n"] = joined["n"].fillna(0).astype("int64")
result = joined.groupby("tier", as_index=False).agg(
    n_customers=("customer_id", "size"), n_contacts=("n", "sum")
)
result.to_parquet("out.parquet", index=False)
