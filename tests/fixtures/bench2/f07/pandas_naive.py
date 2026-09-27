import pandas as pd

products = pd.read_csv("products2.csv")
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
priced = products.merge(prices, on="product_id", how="inner")
result = priced.groupby("category", as_index=False).agg(avg_current_price=("price", "mean"))
result.to_parquet("out.parquet", index=False)
