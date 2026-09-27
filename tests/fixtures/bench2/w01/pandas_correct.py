import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
customers = pd.read_csv("customers2.csv", parse_dates=["signup_date"])
financials = pd.read_csv("order_financials.csv")
paid = orders[orders["status"] == "paid"]
joined = paid.merge(financials, on="order_id", how="inner", validate="one_to_one")
joined = joined.merge(customers, on="customer_id", how="inner", validate="many_to_one")
result = joined.groupby("tier", as_index=False).agg(
    total_gross_rounded=("gross_amount_rounded", "sum")
)
result.to_parquet("out.parquet", index=False)
