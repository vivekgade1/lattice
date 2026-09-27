import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
in_2024 = orders[orders["order_date"].dt.year == 2024]
joined = in_2024.merge(products, on="product_id", how="inner", validate="many_to_one")
joined["discount"] = joined["discount"].fillna(0.0)
result = joined.groupby("category", as_index=False).agg(avg_discount_2024=("discount", "mean"))
result.to_parquet("out.parquet", index=False)
