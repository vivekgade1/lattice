# ruff: noqa
# app/services/vendor_service.py
from sqlmodel import Session
from bench.import_repos.multivendor.app.repositories import vendor_repository
from bench.import_repos.multivendor.app.models.vendor import Vendor
from bench.import_repos.multivendor.app.models.user import User, Role
from bench.import_repos.multivendor.app.schemas.vendor import VendorCreate, VendorUpdate


class VendorError(Exception):
    pass


def register_vendor(session: Session, current_user: User, data: VendorCreate) -> Vendor:
    existing = vendor_repository.get_by_user_id(session, current_user.id)
    if existing is not None:
        raise VendorError("This user is already registered as a vendor")

    new_vendor = Vendor(
        user_id=current_user.id,
        business_name=data.business_name,
    )
    vendor = vendor_repository.create(session, new_vendor)

    current_user.role = Role.VENDOR
    session.add(current_user)
    session.commit()

    return vendor


def get_own_vendor_profile(session: Session, current_user: User) -> Vendor:
    vendor = vendor_repository.get_by_user_id(session, current_user.id)
    if vendor is None:
        raise VendorError("No vendor profile found for this user")
    return vendor


def update_own_vendor_profile(session: Session, current_user: User, data: VendorUpdate) -> Vendor:
    vendor = get_own_vendor_profile(session, current_user)
    update_data = data.model_dump(exclude_unset=True)
    return vendor_repository.update(session, vendor, update_data)


def verify_vendor(session: Session, vendor_id: int, is_verified: bool) -> Vendor:
    vendor = vendor_repository.get_by_id(session, vendor_id)
    if vendor is None:
        raise VendorError("Vendor not found")

    return vendor_repository.update(session, vendor, {"is_verified": is_verified})


from sqlmodel import select
from bench.import_repos.multivendor.app.models.order import Order, OrderStatus
from bench.import_repos.multivendor.app.models.order_item import OrderItem
from bench.import_repos.multivendor.app.models.product import Product
from bench.import_repos.multivendor.app.schemas.vendor import VendorRead


def vendor_dashboard(session: Session, current_user: User) -> dict:
    vendor = get_own_vendor_profile(session, current_user)
    products = list(session.exec(select(Product).where(Product.vendor_id == vendor.id)).all())
    names = {p.id: p.name for p in products}

    by_status = {s.value: 0 for s in OrderStatus}
    units: dict[int, int] = {}
    if products:
        rows = session.exec(
            select(Order.id, Order.status, OrderItem.product_id, OrderItem.quantity)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(OrderItem.product_id.in_(list(names)))
        ).all()
        statuses: dict[int, OrderStatus] = {}
        for order_id, order_status, product_id, quantity in rows:
            statuses[order_id] = order_status
            if order_status != OrderStatus.CANCELLED:
                units[product_id] = units.get(product_id, 0) + quantity
        for order_status in statuses.values():
            by_status[order_status.value] += 1

    best = None
    if units:
        product_id = min(units, key=lambda pid: (-units[pid], pid))
        best = {"product_id": product_id, "name": names[product_id], "units": units[product_id]}

    return {
        "vendor": VendorRead.model_validate(vendor).model_dump(),
        "products": {
            "total": len(products),
            "active": sum(1 for p in products if p.is_active),
            "out_of_stock": sum(1 for p in products if p.is_active and p.stock_quantity <= 0),
        },
        "orders": by_status,
        "units_sold": sum(units.values()),
        "best_seller": best,
    }
