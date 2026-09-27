import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
financials = pd.read_csv("order_financials.csv")
joined = orders.merge(financials, on="order_id", how="inner", validate="one_to_one")
result = joined.groupby("product_id", as_index=False).agg(
    total_unit_shipping=("amount_shipping", "sum"), total_tax_rounded=("tax_amount_rounded", "sum")
)
result.to_parquet("out.parquet", index=False)
