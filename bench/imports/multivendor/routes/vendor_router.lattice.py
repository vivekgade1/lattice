# lattice: v1
# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect
# Lifted from bench.import_repos.multivendor.app.routers.vendor_router by lattice.importer (deterministic lifters and the LLM loop: verify_vendor; every route verified).
@endpoint("PATCH", "/vendors/{vendor_id}/verify", auth=Admin)
def verify_vendor(
    vendor_id: Int, is_verified: Bool, user: CurrentUser
) -> One | NotFound:
    vendors = fetch(Vendor)
    found = where(vendors, col("id") == vendor_id)
    selected = select(found, cols=["id", "user_id", "business_name", "is_verified"])
    vendor = one(selected, else_=NotFound("Vendor not found"))
    updated = update(vendor, is_verified=is_verified)
    return updated
