import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
paid = orders[orders["status"] == "paid"].sort_values("order_date")
priced = pd.merge_asof(
    paid,
    prices.sort_values("valid_from"),
    left_on="order_date",
    right_on="valid_from",
    by="product_id",
    direction="backward",
)
priced["list_revenue"] = priced["qty"] * priced["price"]
result = priced[["order_id", "list_revenue"]]
result.to_parquet("out.parquet", index=False)
