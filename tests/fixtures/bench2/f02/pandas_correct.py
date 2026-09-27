import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
current = (
    prices.sort_values("valid_from")
    .drop_duplicates("product_id", keep="last")
    .rename(columns={"price": "current_price"})[["product_id", "current_price"]]
)
paid = orders[orders["status"] == "paid"]
per_product = paid.groupby("product_id", as_index=False).agg(n_paid_orders=("order_id", "size"))
result = per_product.merge(current, on="product_id", how="inner", validate="one_to_one")
result.to_parquet("out.parquet", index=False)
