import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
contacts = pd.read_csv("customer_contacts.csv")
paid = orders[orders["status"] == "paid"].copy()
paid["revenue"] = paid["qty"] * paid["unit_price"]
per_customer = paid.groupby("customer_id", as_index=False).agg(revenue=("revenue", "sum"))
contact_counts = contacts.groupby("customer_id", as_index=False).agg(n=("contact_id", "size"))
joined = per_customer.merge(contact_counts, on="customer_id", validate="one_to_one")
joined = joined.merge(customers, on="customer_id", validate="one_to_one")
result = joined.groupby("tier", as_index=False).agg(
    paid_revenue=("revenue", "sum"), n_contacts=("n", "sum")
)
result.to_parquet("out.parquet", index=False)
