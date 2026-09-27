import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
financials = pd.read_csv("order_financials.csv")
paid = orders[orders["status"] == "paid"]
joined = paid.merge(financials, on="order_id", how="inner", validate="one_to_one")
joined = joined.merge(products, on="product_id", how="inner", validate="many_to_one")
result = joined.groupby("category", as_index=False).agg(avg_margin=("margin_amount", "mean"))
result.to_parquet("out.parquet", index=False)
