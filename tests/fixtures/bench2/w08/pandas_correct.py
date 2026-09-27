import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
financials = pd.read_csv("order_financials.csv")
joined = orders.merge(financials, on="order_id", how="inner", validate="one_to_one")
result = joined.groupby("status", as_index=False).agg(
    max_unit_discount=("amount_discount", "max")
)
result.to_parquet("out.parquet", index=False)
