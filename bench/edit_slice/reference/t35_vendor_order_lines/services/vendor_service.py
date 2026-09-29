# ruff: noqa
# app/services/vendor_service.py
from sqlmodel import Session, select
from bench.import_repos.multivendor.app.repositories import vendor_repository
from bench.import_repos.multivendor.app.models.vendor import Vendor
from bench.import_repos.multivendor.app.models.user import User, Role
from bench.import_repos.multivendor.app.models.order import Order
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


def vendor_order_lines(session: Session, current_user: User) -> list[dict]:
    vendor = get_own_vendor_profile(session, current_user)
    statement = (
        select(OrderItem, Product, Order)
        .join(Product, Product.id == OrderItem.product_id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Product.vendor_id == vendor.id)
        .order_by(Order.created_at.desc(), OrderItem.id)
    )
    return [
        {
            "order_id": order.id,
            "status": order.status,
            "product_id": product.id,
            "product_name": product.name,
            "quantity": item.quantity,
            "price_at_purchase": item.price_at_purchase,
            "ordered_at": order.created_at,
        }
        for item, product, order in session.exec(statement).all()
    ]
