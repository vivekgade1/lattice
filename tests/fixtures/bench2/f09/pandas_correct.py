import pandas as pd

products = pd.read_csv("products2.csv")
orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
in_2024 = orders[orders["order_date"].dt.year == 2024]
ordered_ids = in_2024[["product_id"]].drop_duplicates()
joined = products.merge(ordered_ids, on="product_id", how="inner", validate="one_to_one")
result = joined.groupby("category", as_index=False).agg(total_cost=("cost", "sum"))
result.to_parquet("out.parquet", index=False)
