# ruff: noqa
# app/repositories/order_repository.py
from sqlmodel import Session, select
from bench.import_repos.multivendor.app.models.order import Order, OrderStatus
from bench.import_repos.multivendor.app.models.order_item import OrderItem


def get_by_id(session: Session, order_id: int) -> Order | None:
    return session.get(Order, order_id)


def get_by_user_id(session: Session, user_id: int) -> list[Order]:
    statement = select(Order).where(Order.user_id == user_id)
    return list(session.exec(statement).all())


def list_all(session: Session, skip: int = 0, limit: int = 20) -> list[Order]:
    statement = select(Order).offset(skip).limit(limit)
    return list(session.exec(statement).all())


def create_order(session: Session, user_id: int) -> Order:
    order = Order(user_id=user_id, status=OrderStatus.PENDING, total_amount=0)
    session.add(order)
    session.flush()
    return order


def add_order_item(
    session: Session, order_id: int, product_id: int, quantity: int, price_at_purchase: float
) -> OrderItem:
    item = OrderItem(
        order_id=order_id,
        product_id=product_id,
        quantity=quantity,
        price_at_purchase=price_at_purchase,
    )
    session.add(item)
    return item


def update_total(session: Session, order: Order, total_amount: float) -> Order:
    order.total_amount = total_amount
    session.add(order)
    return order