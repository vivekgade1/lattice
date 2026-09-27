import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
orders["net_unit_price"] = orders["unit_price"] * (1 - orders["discount"].fillna(0.0))
result = orders.groupby("product_id", as_index=False).agg(
    min_net_unit_price=("net_unit_price", "min")
)
result.to_parquet("out.parquet", index=False)
