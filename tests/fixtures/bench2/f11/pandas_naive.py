import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
paid = orders[orders["status"] == "paid"]
priced = paid.merge(prices, on="product_id", how="inner")
priced = priced[priced["valid_from"] <= priced["order_date"]]
priced = priced.merge(products, on="product_id", how="inner")
priced["value"] = priced["qty"] * priced["price"]
result = priced.groupby("category", as_index=False).agg(list_value=("value", "sum"))
result.to_parquet("out.parquet", index=False)
