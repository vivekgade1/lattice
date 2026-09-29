# ruff: noqa
# app/core/permissions.py
from enum import Enum
from bench.import_repos.multivendor.app.models.user import Role


class Permission(str, Enum):
    MANAGE_OWN_PRODUCTS = "manage_own_products"
    VIEW_ALL_ORDERS = "view_all_orders"
    VIEW_OWN_ORDERS = "view_own_orders"
    MANAGE_ANY_PRODUCT = "manage_any_product"
    VERIFY_VENDOR = "verify_vendor"
    PLACE_ORDER = "place_order"
    UPDATE_ORDER_STATUS = "update_order_status"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.BUYER: {
        Permission.PLACE_ORDER,
        Permission.VIEW_OWN_ORDERS,
    },
    Role.VENDOR: {
        Permission.MANAGE_OWN_PRODUCTS,
        Permission.VIEW_OWN_ORDERS,
        Permission.UPDATE_ORDER_STATUS,
    },
    Role.ADMIN: {
        Permission.VIEW_ALL_ORDERS,
        Permission.MANAGE_ANY_PRODUCT,
        Permission.VERIFY_VENDOR,
        Permission.UPDATE_ORDER_STATUS,
    },
}