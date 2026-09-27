import pandas as pd

products = pd.read_csv("products2.csv")
orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
totals = orders.groupby("product_id", as_index=False).agg(ordered_qty=("qty", "sum"))
joined = products.merge(totals, on="product_id", how="left", validate="one_to_one")
joined["total_qty"] = joined["ordered_qty"].fillna(0).astype("int64")
result = joined[["product_id", "notes", "total_qty"]]
result.to_parquet("out.parquet", index=False)
