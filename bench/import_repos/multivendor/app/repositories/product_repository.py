# ruff: noqa
# app/repositories/product_repository.py
from sqlmodel import Session, select
from sqlalchemy import update as sql_update
from bench.import_repos.multivendor.app.models.product import Product


def get_by_id(session: Session, product_id: int) -> Product | None:
    return session.get(Product, product_id)


def get_by_vendor_id(session: Session, vendor_id: int) -> list[Product]:
    statement = select(Product).where(Product.vendor_id == vendor_id)
    return list(session.exec(statement).all())


def list_products(
    session: Session,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    skip: int = 0,
    limit: int = 20,
) -> list[Product]:
    statement = select(Product).where(Product.is_active == True)

    if category is not None:
        statement = statement.where(Product.category == category)
    if min_price is not None:
        statement = statement.where(Product.price >= min_price)
    if max_price is not None:
        statement = statement.where(Product.price <= max_price)

    statement = statement.offset(skip).limit(limit)
    return list(session.exec(statement).all())


def create(session: Session, product: Product) -> Product:
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


def update_details(session: Session, product: Product, data: dict) -> Product:
    for key, value in data.items():
        setattr(product, key, value)
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


class InsufficientStockError(Exception):
    def __init__(self, product_id: int):
        self.product_id = product_id
        super().__init__(f"Insufficient stock for product {product_id}")


class ConcurrentModificationError(Exception):
    def __init__(self, product_id: int):
        self.product_id = product_id
        super().__init__(f"Product {product_id} was modified concurrently, please retry")


def decrement_stock_optimistic(session: Session, product_id: int, quantity: int) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    if product.stock_quantity < quantity:
        raise InsufficientStockError(product_id)

    current_version = product.version

    statement = (
        sql_update(Product)
        .where(Product.id == product_id, Product.version == current_version)
        .values(
            stock_quantity=Product.stock_quantity - quantity,
            version=Product.version + 1,
        )
    )
    result = session.exec(statement)

    if result.rowcount == 0:
        session.rollback()
        raise ConcurrentModificationError(product_id)

    session.commit()
    session.refresh(product)
    return product


def get_for_update_pessimistic(session: Session, product_id: int) -> Product:
    statement = select(Product).where(Product.id == product_id).with_for_update()
    product = session.exec(statement).first()
    if product is None:
        raise ValueError(f"Product {product_id} not found")
    return product