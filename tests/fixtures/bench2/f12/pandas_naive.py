import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
contacts = pd.read_csv("customer_contacts.csv")
paid = orders[orders["status"] == "paid"].copy()
paid["revenue"] = paid["qty"] * paid["unit_price"]
merged = paid.merge(customers, on="customer_id", how="inner")
merged = merged.merge(contacts, on="customer_id", how="inner")
result = merged.groupby("tier", as_index=False).agg(
    paid_revenue=("revenue", "sum"), n_contacts=("contact_id", "nunique")
)
result.to_parquet("out.parquet", index=False)
