import pandas as pd

orders = pd.read_csv("orders2.csv", parse_dates=["order_date"])
in_2024 = orders[orders["order_date"].dt.year == 2024].copy()
in_2024["month"] = in_2024["order_date"].dt.month
in_2024["rating"] = in_2024["rating"].fillna(0.0)
result = (
    in_2024.groupby("month", as_index=False)
    .agg(avg_rating=("rating", "mean"))
    .sort_values("month")
)
result.to_parquet("out.parquet", index=False)
