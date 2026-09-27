import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
contacts = pd.read_csv("customer_contacts.csv")
order_counts = orders.groupby("customer_id", as_index=False).agg(n_orders=("order_id", "size"))
contact_counts = contacts.groupby("customer_id", as_index=False).agg(
    n_contacts=("contact_id", "size")
)
result = order_counts.merge(contact_counts, on="customer_id", how="inner", validate="one_to_one")
result.to_parquet("out.parquet", index=False)
