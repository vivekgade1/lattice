# ruff: noqa
# app/services/product_service.py
from sqlmodel import Session
from bench.import_repos.multivendor.app.repositories import product_repository, vendor_repository
from bench.import_repos.multivendor.app.models.product import Product
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


class StockMoveError(Exception):
    pass


class NotEnoughStockError(Exception):
    pass


def _own_product(session: Session, vendor_id: int, product_id: int) -> Product:
    product = product_repository.get_by_id(session, product_id)
    if product is None:
        raise ProductNotFoundError("Product not found")
    if product.vendor_id != vendor_id:
        raise ProductPermissionError("You do not own this product")
    return product


def move_stock(
    session: Session, current_user: User, product_id: int, to_product_id: int, quantity: int
) -> tuple[Product, Product]:
    vendor = vendor_repository.get_by_user_id(session, current_user.id)
    if vendor is None:
        raise ProductPermissionError("You do not have a vendor profile")

    source = _own_product(session, vendor.id, product_id)
    target = _own_product(session, vendor.id, to_product_id)
    if source.id == target.id:
        raise StockMoveError("Cannot move stock to the same product")
    if source.stock_quantity < quantity:
        raise NotEnoughStockError("Not enough stock")

    source.stock_quantity -= quantity
    target.stock_quantity += quantity
    session.add(source)
    session.add(target)
    session.commit()
    session.refresh(source)
    session.refresh(target)
    return source, target
