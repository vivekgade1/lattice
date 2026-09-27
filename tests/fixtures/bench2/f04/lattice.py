# lattice: v1
from lattice.ops import agg, group, join, load, write
from lattice.schemas import CustomerContacts, Orders2

orders = load("orders2.csv", schema=Orders2)
contacts = load("customer_contacts.csv", schema=CustomerContacts)
order_counts = group(orders, by=["customer_id"], n_orders=agg.count())
contact_counts = group(contacts, by=["customer_id"], n_contacts=agg.count())
joined = join(order_counts, contact_counts, on=["customer_id"], how="inner")
write(joined, "out.parquet")
