import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
contacts = pd.read_csv("customer_contacts.csv")
merged = orders.merge(contacts, on="customer_id", how="inner")
result = merged.groupby("customer_id", as_index=False).agg(
    n_orders=("order_id", "count"), n_contacts=("contact_id", "nunique")
)
result.to_parquet("out.parquet", index=False)
