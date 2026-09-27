import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
paid = orders[orders["status"] == "paid"].copy()
paid["revenue"] = paid["qty"] * paid["unit_price"]
joined = paid.merge(customers, on="customer_id", how="inner", validate="many_to_one")
per_region = joined.groupby("region", as_index=False, dropna=False).agg(
    revenue=("revenue", "sum")
)
per_region["revenue_share"] = per_region["revenue"] / paid["revenue"].sum()
result = per_region[["region", "revenue_share"]]
result.to_parquet("out.parquet", index=False)
