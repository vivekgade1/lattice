# lattice: v1
from lattice.ops import agg, col, fn, group, join, load, rename, select, where, write
from lattice.schemas import CustomerContacts, Orders2, ProductPrices

orders = load("orders2.csv", schema=Orders2)
contacts = load("customer_contacts.csv", schema=CustomerContacts)
prices = load("product_prices.csv", schema=ProductPrices)
latest = group(prices, by=["product_id"], valid_from=agg.max(col("valid_from")))
current = join(latest, prices, on=["product_id", "valid_from"], how="inner")
current_prices = rename(current, price="current_price")
primary = where(contacts, col("is_primary"))
primary_emails = group(primary, by=["customer_id"], primary_email=agg.max(col("email")))
december = where(
    orders,
    (col("status") == "paid")
    & (fn.year(col("order_date")) == 2024)
    & (fn.month(col("order_date")) == 12),
)
with_email = join(december, primary_emails, on=["customer_id"], how="inner")
priced = join(with_email, current_prices, on=["product_id"], how="inner")
result = select(priced, cols=["order_id", "primary_email", "current_price"])
write(result, "out.parquet")
