# lattice: v1
from lattice.ops import agg, coalesce, col, group, join, load, write
from lattice.schemas import CustomerContacts, Customers2

customers = load("customers2.csv", schema=Customers2)
contacts = load("customer_contacts.csv", schema=CustomerContacts)
contact_counts = group(contacts, by=["customer_id"], n=agg.count())
joined = join(customers, contact_counts, on=["customer_id"], how="left")
per_tier = group(
    joined,
    by=["tier"],
    n_customers=agg.count(),
    n_contacts=agg.sum(coalesce(col("n"), 0)),
)
write(per_tier, "out.parquet")
