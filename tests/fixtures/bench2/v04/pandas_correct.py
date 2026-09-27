import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
net_unit = orders["unit_price"] * (1 - orders["discount"].fillna(0.0))
result = pd.DataFrame({"avg_net_unit_price": [net_unit.mean()]})
result.to_parquet("out.parquet", index=False)
