# ruff: noqa
# app/services/order_service.py
from sqlmodel import Session
from bench.import_repos.multivendor.app.repositories import order_repository, product_repository
from bench.import_repos.multivendor.app.repositories.product_repository import InsufficientStockError, ConcurrentModificationError
from bench.import_repos.multivendor.app.models.order import Order, OrderStatus
from bench.import_repos.multivendor.app.models.user import User
from bench.import_repos.multivendor.app.models.product import Product
from bench.import_repos.multivendor.app.schemas.order import OrderCreate


class OrderError(Exception):
    pass


class OrderNotFoundError(Exception):
    pass


class OrderPermissionError(Exception):
    pass


VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}


def place_order(session: Session, current_user: User, data: OrderCreate) -> Order:
    try:
        order = order_repository.create_order(session, current_user.id)

        total = 0.0
        for item in data.items:
            product = product_repository.decrement_stock_optimistic(
                session, item.product_id, item.quantity
            )
            order_repository.add_order_item(
                session,
                order_id=order.id,
                product_id=product.id,
                quantity=item.quantity,
                price_at_purchase=product.price,
            )
            total += product.price * item.quantity

        order_repository.update_total(session, order, total)
        session.commit()
        session.refresh(order)
        return order

    except (InsufficientStockError, ConcurrentModificationError):
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise


def get_order(session: Session, order_id: int) -> Order:
    order = order_repository.get_by_id(session, order_id)
    if order is None:
        raise OrderNotFoundError("Order not found")
    return order


def get_own_order(session: Session, current_user: User, order_id: int) -> Order:
    order = get_order(session, order_id)
    if order.user_id != current_user.id:
        raise OrderPermissionError("You do not have access to this order")
    return order


def list_own_orders(session: Session, current_user: User) -> list[Order]:
    return order_repository.get_by_user_id(session, current_user.id)


def transition_order_status(session: Session, order_id: int, new_status: OrderStatus) -> Order:
    order = get_order(session, order_id)

    allowed_next_states = VALID_TRANSITIONS[order.status]
    if new_status not in allowed_next_states:
        raise OrderError(
            f"Cannot transition order from {order.status.value} to {new_status.value}"
        )

    order.status = new_status
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


# add to app/services/order_service.py
from bench.import_repos.multivendor.app.repositories.product_repository import get_for_update_pessimistic


def purchase_single_item_pessimistic(
    session: Session, current_user: User, product_id: int, quantity: int
) -> Order:
    try:
        product = get_for_update_pessimistic(session, product_id)

        if product.stock_quantity < quantity:
            raise InsufficientStockError(product_id)

        product.stock_quantity -= quantity
        session.add(product)

        order = order_repository.create_order(session, current_user.id)
        order_repository.add_order_item(
            session,
            order_id=order.id,
            product_id=product.id,
            quantity=quantity,
            price_at_purchase=product.price,
        )
        total = product.price * quantity
        order_repository.update_total(session, order, total)

        session.commit()
        session.refresh(order)
        return order

    except Exception:
        session.rollback()
        raise


def edit_order_items(session: Session, current_user: User, order_id: int, data: OrderCreate) -> Order:
    order = get_own_order(session, current_user, order_id)
    if order.status != OrderStatus.PENDING:
        raise OrderError("Only pending orders can be changed")

    held: dict[int, int] = {}
    for item in order.items:
        held[item.product_id] = held.get(item.product_id, 0) + item.quantity
    needed: dict[int, int] = {}
    for item in data.items:
        needed[item.product_id] = needed.get(item.product_id, 0) + item.quantity
    products = {pid: session.get(Product, pid) for pid in {*held, *needed}}
    for pid, quantity in needed.items():
        if products[pid].stock_quantity + held.get(pid, 0) < quantity:
            raise InsufficientStockError(pid)

    for pid, product in products.items():
        product.stock_quantity += held.get(pid, 0) - needed.get(pid, 0)
        session.add(product)
    order.items.clear()  # delete-orphan: the old lines are deleted
    session.flush()
    total = 0.0
    for item in data.items:
        price = products[item.product_id].price
        order_repository.add_order_item(
            session,
            order_id=order.id,
            product_id=item.product_id,
            quantity=item.quantity,
            price_at_purchase=price,
        )
        total += price * item.quantity
    order_repository.update_total(session, order, total)
    session.commit()
    session.refresh(order)
    return order
