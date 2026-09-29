# ruff: noqa
# app/services/product_service.py
from sqlmodel import Session, select
from bench.import_repos.multivendor.app.repositories import product_repository, vendor_repository
from bench.import_repos.multivendor.app.models.product import Product
from bench.import_repos.multivendor.app.models.order import Order, OrderStatus
from bench.import_repos.multivendor.app.models.order_item import OrderItem
from bench.import_repos.multivendor.app.models.user import User
from bench.import_repos.multivendor.app.schemas.product import ProductCreate, ProductUpdate


class ProductNotFoundError(Exception):
    pass


class ProductPermissionError(Exception):
    pass


def create_product(session: Session, current_user: User, data: ProductCreate) -> Product:
    vendor = vendor_repository.get_by_user_id(session, current_user.id)
    if vendor is None:
        raise ProductPermissionError("You must have a vendor profile to create products")

    new_product = Product(
        vendor_id=vendor.id,
        name=data.name,
        description=data.description,
        category=data.category,
        price=data.price,
        stock_quantity=data.stock_quantity,
    )
    return product_repository.create(session, new_product)


def get_product(session: Session, product_id: int) -> Product:
    product = product_repository.get_by_id(session, product_id)
    if product is None:
        raise ProductNotFoundError("Product not found")
    return product


def list_products(
    session: Session,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    skip: int = 0,
    limit: int = 20,
) -> list[Product]:
    return product_repository.list_products(session, category, min_price, max_price, skip, limit)


def update_own_product(
    session: Session, current_user: User, product_id: int, data: ProductUpdate
) -> Product:
    vendor = vendor_repository.get_by_user_id(session, current_user.id)
    if vendor is None:
        raise ProductPermissionError("You do not have a vendor profile")

    product = product_repository.get_by_id(session, product_id)
    if product is None:
        raise ProductNotFoundError("Product not found")

    if product.vendor_id != vendor.id:
        raise ProductPermissionError("You do not own this product")

    update_data = data.model_dump(exclude_unset=True)
    return product_repository.update_details(session, product, update_data)


def top_sellers(session: Session, category: str | None = None) -> list[dict]:
    statement = select(Product).where(Product.is_active == True)
    if category is not None:
        statement = statement.where(Product.category == category)
    products = session.exec(statement).all()
    sold = session.exec(
        select(OrderItem.product_id, OrderItem.quantity)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.status != OrderStatus.CANCELLED)
    ).all()
    units: dict[int, int] = {}
    for product_id, quantity in sold:
        units[product_id] = units.get(product_id, 0) + quantity
    rows = [
        {"product_id": p.id, "name": p.name, "units_sold": units.get(p.id, 0)}
        for p in products
    ]
    return sorted(rows, key=lambda r: (-r["units_sold"], r["product_id"]))
