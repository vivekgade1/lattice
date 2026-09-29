# ruff: noqa
# app/services/vendor_service.py
from datetime import date, datetime, time, timedelta

from sqlmodel import Session, select
from bench.import_repos.multivendor.app.repositories import vendor_repository
from bench.import_repos.multivendor.app.models.vendor import Vendor
from bench.import_repos.multivendor.app.models.user import User, Role
from bench.import_repos.multivendor.app.models.order import Order, OrderStatus
from bench.import_repos.multivendor.app.models.order_item import OrderItem
from bench.import_repos.multivendor.app.models.product import Product
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


def vendor_sales(
    session: Session, current_user: User, start: date | None, end: date | None
) -> dict:
    vendor = get_own_vendor_profile(session, current_user)
    statement = (
        select(OrderItem.order_id, OrderItem.quantity, OrderItem.price_at_purchase)
        .join(Product, Product.id == OrderItem.product_id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Product.vendor_id == vendor.id, Order.status != OrderStatus.CANCELLED)
    )
    if start is not None:
        statement = statement.where(Order.created_at >= datetime.combine(start, time.min))
    if end is not None:
        day_after = datetime.combine(end + timedelta(days=1), time.min)
        statement = statement.where(Order.created_at < day_after)
    rows = session.exec(statement).all()
    return {
        "orders": len({order_id for order_id, _, _ in rows}),
        "units": sum(quantity for _, quantity, _ in rows),
        "revenue": float(sum(quantity * price for _, quantity, price in rows)),
    }
