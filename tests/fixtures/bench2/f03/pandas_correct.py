import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
original = (
    prices.sort_values("valid_from")
    .drop_duplicates("product_id", keep="first")
    .rename(columns={"price": "original_price"})[["product_id", "original_price"]]
)
paid = orders[orders["status"] == "paid"]
per_product = paid.groupby("product_id", as_index=False).agg(paid_qty=("qty", "sum"))
result = per_product.merge(original, on="product_id", how="inner", validate="one_to_one")
result.to_parquet("out.parquet", index=False)
