import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
paid = orders[orders["status"] == "paid"]
priced = paid.merge(prices, on="product_id", how="inner")
priced = priced[priced["valid_from"] <= priced["order_date"]].copy()
priced["list_revenue"] = priced["qty"] * priced["price"]
result = priced[["order_id", "list_revenue"]]
result.to_parquet("out.parquet", index=False)
