import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
financials = pd.read_csv("order_financials.csv")
in_2024 = orders[orders["order_date"].dt.year == 2024]
joined = in_2024.merge(financials, on="order_id", how="inner", validate="one_to_one")
result = joined[["order_id", "amount_net"]].rename(columns={"amount_net": "unit_net"})
result.to_parquet("out.parquet", index=False)
