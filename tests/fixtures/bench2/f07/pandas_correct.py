import pandas as pd

products = pd.read_csv("products2.csv")
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
current = prices.sort_values("valid_from").drop_duplicates("product_id", keep="last")
priced = products.merge(current, on="product_id", how="inner", validate="one_to_one")
result = priced.groupby("category", as_index=False).agg(avg_current_price=("price", "mean"))
result.to_parquet("out.parquet", index=False)
