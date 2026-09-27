import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
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
priced = priced.merge(products, on="product_id", how="inner", validate="many_to_one")
priced["value"] = priced["qty"] * priced["price"]
result = priced.groupby("category", as_index=False).agg(list_value=("value", "sum"))
result.to_parquet("out.parquet", index=False)
