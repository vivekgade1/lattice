import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
financials = pd.read_csv("order_financials.csv")
refunded = orders[orders["status"] == "refunded"]
joined = refunded.merge(financials, on="order_id", how="inner", validate="one_to_one")
result = joined[["order_id", "refund_amount", "amount_refund"]].rename(
    columns={"refund_amount": "refund_total", "amount_refund": "refund_per_unit"}
)
result.to_parquet("out.parquet", index=False)
