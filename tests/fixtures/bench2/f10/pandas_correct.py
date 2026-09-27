import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
contacts = pd.read_csv("customer_contacts.csv")
prices = pd.read_csv("product_prices.csv", parse_dates=["valid_from"])
current = (
    prices.sort_values("valid_from")
    .drop_duplicates("product_id", keep="last")
    .rename(columns={"price": "current_price"})[["product_id", "current_price"]]
)
primary = contacts[contacts["is_primary"]][["customer_id", "email"]].rename(
    columns={"email": "primary_email"}
)
december = orders[
    (orders["status"] == "paid")
    & (orders["order_date"].dt.year == 2024)
    & (orders["order_date"].dt.month == 12)
]
joined = december.merge(primary, on="customer_id", how="inner", validate="many_to_one")
joined = joined.merge(current, on="product_id", how="inner", validate="many_to_one")
result = joined[["order_id", "primary_email", "current_price"]]
result.to_parquet("out.parquet", index=False)
