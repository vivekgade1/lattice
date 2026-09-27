import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
paid = orders[orders["status"] == "paid"]
merged = paid.merge(prices, on="product_id", how="inner").sort_values("valid_from")
result = merged.groupby("product_id", as_index=False).agg(
    n_paid_orders=("order_id", "size"), current_price=("price", "last")
)
result.to_parquet("out.parquet", index=False)
