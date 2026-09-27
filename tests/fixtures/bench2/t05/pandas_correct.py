import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"], dtype={"zip": str})
paid = orders[orders["status"] == "paid"].copy()
paid["revenue"] = paid["qty"] * paid["unit_price"]
joined = paid.merge(customers, on="customer_id", how="inner", validate="many_to_one")
result = joined.groupby("zip", as_index=False).agg(paid_revenue=("revenue", "sum"))
result.to_parquet("out.parquet", index=False)
