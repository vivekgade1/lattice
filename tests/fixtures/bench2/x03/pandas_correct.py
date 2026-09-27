import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
products = pd.read_csv("products2.csv")
paid = orders[orders["status"] == "paid"].copy()
paid["revenue"] = paid["qty"] * paid["unit_price"]
per_product = paid.groupby("product_id", as_index=False).agg(
    n_paid_orders=("order_id", "size"), paid_revenue=("revenue", "sum")
)
named = per_product.merge(products, on="product_id", how="inner", validate="one_to_one")
result = named[["product_name", "n_paid_orders", "paid_revenue"]]
result.to_parquet("out.parquet", index=False)
