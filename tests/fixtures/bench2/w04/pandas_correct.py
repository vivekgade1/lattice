import pandas as pd

financials = pd.read_csv("order_financials.csv")
narrow = financials[["order_id", "total_amount_rounded"]].rename(
    columns={"total_amount_rounded": "total_rounded"}
)
ranked = narrow.sort_values(["total_rounded", "order_id"], ascending=[False, True])
result = ranked.head(10)
result.to_parquet("out.parquet", index=False)
