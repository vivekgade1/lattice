import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
per_product = orders.groupby("product_id", as_index=False).agg(
    n_customers=("customer_id", "nunique")
)
named = per_product.merge(products, on="product_id", how="inner", validate="one_to_one")
result = named[["product_name", "n_customers"]]
result.to_parquet("out.parquet", index=False)
