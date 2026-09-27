import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
current = (
    prices.sort_values("valid_from")
    .drop_duplicates("product_id", keep="last")
    .rename(columns={"price": "current_price"})[["product_id", "current_price"]]
)
paid = orders[orders["status"] == "paid"]
priced = paid.merge(current, on="product_id", how="inner", validate="many_to_one")
priced = priced.merge(products, on="product_id", how="inner", validate="many_to_one")
priced["value"] = priced["qty"] * priced["current_price"]
result = priced.groupby("category", as_index=False).agg(value_at_current_price=("value", "sum"))
result.to_parquet("out.parquet", index=False)
