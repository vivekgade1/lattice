import pandas as pd

products = pd.read_csv("products2.csv")
orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
in_2024 = orders[orders["order_date"].dt.year == 2024]
joined = products.merge(in_2024, on="product_id", how="inner")
result = joined.groupby("category", as_index=False).agg(total_cost=("cost", "sum"))
result.to_parquet("out.parquet", index=False)
