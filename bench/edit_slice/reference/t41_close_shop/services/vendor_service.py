# ruff: noqa
# app/services/vendor_service.py
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


class ShopBusyError(Exception):
    pass


def close_shop(session: Session, current_user: User) -> dict:
    vendor = get_own_vendor_profile(session, current_user)
    products = session.exec(select(Product).where(Product.vendor_id == vendor.id)).all()
    product_ids = [p.id for p in products]
    orders: dict[int, Order] = {}
    if product_ids:
        statement = (
            select(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(OrderItem.product_id.in_(product_ids))
        )
        for order in session.exec(statement).all():
            orders[order.id] = order
    if any(o.status in (OrderStatus.CONFIRMED, OrderStatus.SHIPPED) for o in orders.values()):
        raise ShopBusyError("Shop has orders being fulfilled")

    deactivated = 0
    for product in products:
        if product.is_active:
            product.is_active = False
            deactivated += 1
            session.add(product)
    cancelled = 0
    for order in orders.values():
        if order.status != OrderStatus.PENDING:
            continue
        for item in order.items:
            stocked = session.get(Product, item.product_id)
            stocked.stock_quantity += item.quantity
            session.add(stocked)
        order.status = OrderStatus.CANCELLED
        session.add(order)
        cancelled += 1
    session.commit()
    return {"products_deactivated": deactivated, "orders_cancelled": cancelled}
