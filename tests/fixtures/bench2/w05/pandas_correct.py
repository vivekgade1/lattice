import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
financials = pd.read_csv("order_financials.csv")
in_2024 = orders[orders["order_date"].dt.year == 2024].copy()
in_2024["month"] = in_2024["order_date"].dt.month
joined = in_2024.merge(financials, on="order_id", how="inner", validate="one_to_one")
result = (
    joined.groupby("month", as_index=False)
    .agg(fee_usd=("fee_amount_usd", "sum"))
    .sort_values("month")
)
result.to_parquet("out.parquet", index=False)
