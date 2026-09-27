import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
paid = orders[orders["status"] == "paid"].copy()
paid["revenue"] = paid["qty"] * paid["unit_price"]
per_product = paid.groupby("product_id", as_index=False).agg(paid_revenue=("revenue", "sum"))
top5 = per_product.sort_values(["paid_revenue", "product_id"], ascending=[False, True]).head(5)
named = top5.merge(products, on="product_id", how="inner", validate="one_to_one")
result = named[["product_id", "product_name", "notes", "paid_revenue"]]
result.to_parquet("out.parquet", index=False)
