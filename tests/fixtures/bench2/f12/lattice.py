# lattice: v1
from lattice.ops import agg, col, derive, group, join, load, where, write
from lattice.schemas import CustomerContacts, Customers2, Orders2

orders = load("orders2.csv", schema=Orders2)
customers = load("customers2.csv", schema=Customers2)
contacts = load("customer_contacts.csv", schema=CustomerContacts)
paid = where(orders, col("status") == "paid")
with_revenue = derive(paid, revenue=col("qty") * col("unit_price"))
per_customer = group(with_revenue, by=["customer_id"], revenue=agg.sum(col("revenue")))
contact_counts = group(contacts, by=["customer_id"], n=agg.count())
with_contacts = join(per_customer, contact_counts, on=["customer_id"], how="inner")
with_tier = join(with_contacts, customers, on=["customer_id"], how="inner")
per_tier = group(
    with_tier,
    by=["tier"],
    paid_revenue=agg.sum(col("revenue")),
    n_contacts=agg.sum(col("n")),
)
write(per_tier, "out.parquet")
