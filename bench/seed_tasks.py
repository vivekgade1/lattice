"""30 seed benchmark tasks with polars reference solutions.

Every reference function takes a mapping of dataset name -> polars.DataFrame
(as produced by ``bench.datasets.load_fixtures``) and returns the expected
output DataFrame for that task, following the exact semantics stated in the
task's prompt.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping

import polars as pl

from bench.tasks import Task

# ---------------------------------------------------------------------------
# single-table tasks
# ---------------------------------------------------------------------------


def _s01(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    return frames["orders"].filter(pl.col("status") == "paid")


def _s02(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.with_columns((pl.col("qty") * pl.col("unit_price")).alias("revenue"))


def _s03(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    return frames["customers"].filter(pl.col("region") == "west")


def _s04(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    return frames["orders"].filter(pl.col("qty") >= 5)


def _s05(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    net_price = (pl.col("unit_price") * (1 - pl.col("discount").fill_null(0.0))).alias(
        "net_price"
    )
    return orders.select(pl.col("order_id"), net_price)


def _s06(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    return frames["orders"].select("order_id", "customer_id", "status")


def _s07(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    return frames["orders"].rename({"qty": "quantity"})


def _s08(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.select("order_id", "unit_price")
        .sort(["unit_price", "order_id"], descending=[True, True])
        .head(10)
    )


def _s09(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    return frames["orders"].group_by("status").agg(pl.len().alias("n_orders"))


def _s10(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(
        pl.col("order_id"), pl.col("order_date").dt.year().alias("order_year")
    )


# ---------------------------------------------------------------------------
# multi-table (single physical table, multiple operations) tasks
# ---------------------------------------------------------------------------


def _m01(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.filter(pl.col("status") == "paid")
        .group_by("customer_id")
        .agg((pl.col("qty") * pl.col("unit_price")).sum().alias("total_revenue"))
    )


def _m02(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.filter(pl.col("status") == "paid")
        .group_by("product_id")
        .agg(
            (pl.col("qty") * pl.col("unit_price")).sum().alias("total_revenue"),
            pl.col("qty").mean().alias("mean_qty"),
        )
        .filter(pl.col("total_revenue") > 500)
    )


def _m03(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    bucket = (
        pl.when(pl.col("qty") >= 10)
        .then(pl.lit("large"))
        .when(pl.col("qty") >= 3)
        .then(pl.lit("medium"))
        .otherwise(pl.lit("small"))
        .alias("size")
    )
    return orders.with_columns(bucket).group_by("size").agg(pl.len().alias("n_orders"))


def _m04(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.filter(
            (pl.col("status") == "paid") & (pl.col("order_date").dt.year() == 2024)
        )
        .with_columns(pl.col("order_date").dt.month().alias("month"))
        .group_by("month")
        .agg((pl.col("qty") * pl.col("unit_price")).sum().alias("revenue"))
        .sort("month")
    )


def _m05(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns((pl.col("discount").fill_null(0.0) > 0).alias("has_discount"))
        .group_by("has_discount")
        .agg(
            pl.len().alias("n_orders"),
            pl.col("unit_price").mean().alias("mean_unit_price"),
        )
    )


def _m06(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    return (
        customers.with_columns(pl.col("signup_date").dt.year().alias("signup_year"))
        .group_by(["tier", "signup_year"])
        .agg(pl.len().alias("n_customers"))
    )


def _m07(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.group_by("customer_id")
        .agg(
            pl.len().alias("total_orders"),
            (pl.col("status") == "cancelled").sum().cast(pl.Int64).alias("cancelled_orders"),
        )
        .with_columns(
            (pl.col("cancelled_orders") / pl.col("total_orders")).alias("cancel_rate")
        )
    )


def _m08(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.filter(pl.col("status") == "paid")
        .group_by("customer_id")
        .agg((pl.col("qty") * pl.col("unit_price")).sum().alias("total_revenue"))
        .filter(pl.col("total_revenue") >= 1000)
        .sort(["total_revenue", "customer_id"], descending=[True, True])
        .head(5)
    )


def _m09(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    net_revenue = (
        pl.col("qty") * pl.col("unit_price") * (1 - pl.col("discount").fill_null(0.0))
    )
    return orders.group_by("status").agg(net_revenue.sum().round(2).alias("net_revenue"))


def _m10(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    stripped = pl.col("name").str.strip_chars()
    return customers.select(
        pl.col("customer_id"),
        stripped.str.to_uppercase().alias("name_upper"),
        stripped.str.len_chars().cast(pl.Int64).alias("name_len"),
    )


def _m11(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    start = datetime.date(2024, 10, 1)
    end = datetime.date(2024, 12, 31)
    return (
        orders.filter((pl.col("order_date") >= start) & (pl.col("order_date") <= end))
        .group_by("status")
        .agg(pl.len().alias("n_orders"))
    )


def _m12(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    target = datetime.date(2025, 1, 1)
    days = (pl.lit(target) - pl.col("signup_date")).dt.total_days().alias("days_since_signup")
    return customers.select(pl.col("customer_id"), days)


# ---------------------------------------------------------------------------
# join tasks
# ---------------------------------------------------------------------------


def _j01(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.join(customers.select("customer_id", "region"), on="customer_id", how="left")
    return joined.select("order_id", "region")


def _j02(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.filter(pl.col("status") == "paid").join(
        customers.select("customer_id", "region"), on="customer_id", how="left"
    )
    return joined.group_by("region").agg(
        (pl.col("qty") * pl.col("unit_price")).sum().alias("revenue")
    )


def _j03(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.join(products.select("product_id", "category"), on="product_id", how="left")
    return joined.group_by("category").agg(
        (pl.col("qty") * pl.col("unit_price")).sum().alias("revenue")
    )


def _j04(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.filter(pl.col("status") == "paid").join(products, on="product_id", how="left")
    margin = ((pl.col("unit_price") - pl.col("cost")) * pl.col("qty")).sum().alias("margin")
    return (
        joined.group_by(["product_id", "product_name"])
        .agg(margin)
        .sort(["margin", "product_id"], descending=[True, True])
        .head(5)
        .select("product_name", "margin")
    )


def _j05(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    orders = frames["orders"]
    ordered_customer_ids = orders.select("customer_id").unique()
    return customers.join(ordered_customer_ids, on="customer_id", how="anti").select(
        "customer_id", "name"
    )


def _j06(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.join(customers.select("customer_id", "tier"), on="customer_id", how="left")
    return joined.group_by("tier").agg(pl.len().alias("n_orders"))


def _j07(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    products = frames["products"]
    joined = (
        orders.filter(pl.col("status") == "paid")
        .join(customers.select("customer_id", "region"), on="customer_id", how="left")
        .join(products.select("product_id", "category"), on="product_id", how="left")
    )
    return joined.group_by(["region", "category"]).agg(
        (pl.col("qty") * pl.col("unit_price")).sum().alias("revenue")
    )


def _j08(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.filter(pl.col("status") == "paid").join(
        customers.select("customer_id", "region"), on="customer_id", how="left"
    )
    return (
        joined.with_columns((pl.col("qty") * pl.col("unit_price")).alias("order_value"))
        .group_by("region")
        .agg(pl.col("order_value").mean().round(2).alias("avg_order_value"))
    )


SEED_TASKS: tuple[Task, ...] = (
    Task(
        id="s01",
        tier="single",
        prompt=(
            'Using the orders table, filter to rows where status equals "paid". Return '
            "every order column unchanged (order_id, customer_id, product_id, qty, "
            "unit_price, status, order_date, discount). Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s01,
    ),
    Task(
        id="s02",
        tier="single",
        prompt=(
            "Using the orders table, add a new column revenue = qty * unit_price to every "
            "row (no filtering). Return all original order columns plus revenue. Row order "
            "does not matter."
        ),
        inputs=("orders",),
        reference=_s02,
    ),
    Task(
        id="s03",
        tier="single",
        prompt=(
            'Using the customers table, keep only rows where region equals "west" '
            "(customers with a null region are excluded). Return all customer columns "
            "unchanged (customer_id, name, region, signup_date, tier). Row order does not "
            "matter."
        ),
        inputs=("customers",),
        reference=_s03,
    ),
    Task(
        id="s04",
        tier="single",
        prompt=(
            "Using the orders table, keep only rows where qty is greater than or equal to "
            "5. Return all order columns unchanged. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s04,
    ),
    Task(
        id="s05",
        tier="single",
        prompt=(
            "Using the orders table, compute net_price = unit_price * (1 - discount) for "
            "every row, treating a missing (null) discount as 0. Return exactly two "
            "columns: order_id, net_price. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s05,
    ),
    Task(
        id="s06",
        tier="single",
        prompt=(
            "Using the orders table, return exactly three columns for every row: "
            "order_id, customer_id, status. No filtering. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s06,
    ),
    Task(
        id="s07",
        tier="single",
        prompt=(
            "Using the orders table, rename the qty column to quantity. Return all order "
            "columns under their new names (order_id, customer_id, product_id, quantity, "
            "unit_price, status, order_date, discount). Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s07,
    ),
    Task(
        id="s08",
        tier="single",
        prompt=(
            "Using the orders table, find the 10 orders with the highest unit_price. "
            "Break ties by order_id descending (higher order_id first). Return exactly "
            "two columns: order_id, unit_price, sorted by unit_price descending (and by "
            "order_id descending among ties). Row order matters and must match this sort "
            "exactly."
        ),
        inputs=("orders",),
        reference=_s08,
        ordered=True,
    ),
    Task(
        id="s09",
        tier="single",
        prompt=(
            "Using the orders table, count the number of orders for each status value. "
            "Return exactly two columns: status, n_orders. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s09,
    ),
    Task(
        id="s10",
        tier="single",
        prompt=(
            "Using the orders table, extract the calendar year from order_date for every "
            "row. Return exactly two columns: order_id, order_year. Row order does not "
            "matter."
        ),
        inputs=("orders",),
        reference=_s10,
    ),
    Task(
        id="m01",
        tier="multi",
        prompt=(
            'Using the orders table, keep only rows where status equals "paid", then for '
            "each customer_id compute total_revenue = sum(qty * unit_price) over those "
            "rows. Return exactly two columns: customer_id, total_revenue. Customers with "
            "no paid orders are omitted. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m01,
    ),
    Task(
        id="m02",
        tier="multi",
        prompt=(
            'Using the orders table, keep only rows where status equals "paid". For each '
            "product_id compute total_revenue = sum(qty * unit_price) and mean_qty = "
            "mean(qty) over those rows, then keep only products where total_revenue is "
            "greater than 500. Return exactly three columns: product_id, total_revenue, "
            "mean_qty. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m02,
    ),
    Task(
        id="m03",
        tier="multi",
        prompt=(
            "Using the orders table, classify every order into a size bucket by qty: "
            '"large" if qty >= 10, otherwise "medium" if qty >= 3, otherwise "small" '
            "(qty 0, 1, or 2). Count the number of orders in each bucket. Return exactly "
            "two columns: size, n_orders. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m03,
    ),
    Task(
        id="m04",
        tier="multi",
        prompt=(
            'Using the orders table, keep only rows where status equals "paid" and '
            "order_date falls in calendar year 2024. Extract the calendar month (1-12) "
            "from order_date and compute revenue = sum(qty * unit_price) per month. "
            "Return exactly two columns: month, revenue, sorted by month ascending. Row "
            "order matters and must be ascending by month."
        ),
        inputs=("orders",),
        reference=_m04,
        ordered=True,
    ),
    Task(
        id="m05",
        tier="multi",
        prompt=(
            "Using the orders table, define has_discount as true when the discount value "
            "(with a missing/null discount treated as 0) is greater than 0, and false "
            "otherwise. For each value of has_discount, compute n_orders = count of "
            "orders and mean_unit_price = mean(unit_price). Return exactly three columns: "
            "has_discount, n_orders, mean_unit_price. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m05,
    ),
    Task(
        id="m06",
        tier="multi",
        prompt=(
            "Using the customers table, extract the calendar year from signup_date as "
            "signup_year. For each combination of tier and signup_year, count the number "
            "of customers. Return exactly three columns: tier, signup_year, n_customers. "
            "Row order does not matter."
        ),
        inputs=("customers",),
        reference=_m06,
    ),
    Task(
        id="m07",
        tier="multi",
        prompt=(
            "Using the orders table (all statuses, no filtering), for each customer_id "
            "compute total_orders = count of all their orders, cancelled_orders = count "
            'of their orders with status equal to "cancelled", and cancel_rate = '
            "cancelled_orders / total_orders (as a float). Return exactly four columns: "
            "customer_id, total_orders, cancelled_orders, cancel_rate. Row order does not "
            "matter."
        ),
        inputs=("orders",),
        reference=_m07,
    ),
    Task(
        id="m08",
        tier="multi",
        prompt=(
            'Using the orders table, keep only rows where status equals "paid", then for '
            "each customer_id compute total_revenue = sum(qty * unit_price). Keep only "
            "customers where total_revenue is greater than or equal to 1000, then return "
            "the top 5 by total_revenue descending, breaking ties by customer_id "
            "ascending. Return exactly two columns: customer_id, total_revenue, sorted by "
            "total_revenue descending (ties by customer_id ascending). Row order matters "
            "and must match this sort exactly."
        ),
        inputs=("orders",),
        reference=_m08,
        ordered=True,
    ),
    Task(
        id="m09",
        tier="multi",
        prompt=(
            "Using the orders table, compute net_revenue per status = sum(qty * "
            "unit_price * (1 - discount)) over all orders with that status, treating a "
            "missing (null) discount as 0, rounded to 2 decimal places. Return exactly "
            "two columns: status, net_revenue. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m09,
    ),
    Task(
        id="m10",
        tier="multi",
        prompt=(
            "Using the customers table, strip leading and trailing whitespace from name. "
            "Compute name_upper = the stripped name in uppercase, and name_len = the "
            "character length of the stripped name (before uppercasing). Return exactly "
            "three columns: customer_id, name_upper, name_len. Row order does not matter."
        ),
        inputs=("customers",),
        reference=_m10,
    ),
    Task(
        id="m11",
        tier="multi",
        prompt=(
            "Using the orders table, keep only rows where order_date falls between "
            "2024-10-01 and 2024-12-31 inclusive (Q4 2024). Count the number of orders "
            "for each status value among those rows. Return exactly two columns: status, "
            "n_orders. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m11,
    ),
    Task(
        id="m12",
        tier="multi",
        prompt=(
            "Using the customers table, compute days_since_signup = the number of whole "
            "days from signup_date to 2025-01-01 (2025-01-01 minus signup_date, as an "
            "integer number of days). Return exactly two columns: customer_id, "
            "days_since_signup. Row order does not matter."
        ),
        inputs=("customers",),
        reference=_m12,
    ),
    Task(
        id="j01",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id. For every "
            "order, return exactly two columns: order_id, region (the region of that "
            "order's customer; null if the customer's region is null). Row order does "
            "not matter."
        ),
        inputs=("orders", "customers"),
        reference=_j01,
    ),
    Task(
        id="j02",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, then keep only "
            'rows where status equals "paid". For each region (customers with a null '
            "region form their own null-region group) compute revenue = sum(qty * "
            "unit_price). Return exactly two columns: region, revenue. Row order does not "
            "matter."
        ),
        inputs=("orders", "customers"),
        reference=_j02,
    ),
    Task(
        id="j03",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, using all orders "
            "regardless of status (no status filtering). For each category compute "
            "revenue = sum(qty * unit_price). Return exactly two columns: category, "
            "revenue. Row order does not matter."
        ),
        inputs=("orders", "products"),
        reference=_j03,
    ),
    Task(
        id="j04",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, then keep only "
            'rows where status equals "paid". For each product compute margin = '
            "sum((unit_price - cost) * qty). Return the top 5 products by margin "
            "descending, breaking ties by product_id descending (the product_id itself is "
            "not part of the output). Return exactly two columns: product_name, margin, "
            "sorted by margin descending. Row order matters and must match this sort "
            "exactly."
        ),
        inputs=("orders", "products"),
        reference=_j04,
        ordered=True,
    ),
    Task(
        id="j05",
        tier="join",
        prompt=(
            "Using the customers and orders tables, find customers who have zero orders "
            "(no row in the orders table references their customer_id). Return exactly "
            "two columns: customer_id, name (the raw, unstripped name). Row order does "
            "not matter."
        ),
        inputs=("customers", "orders"),
        reference=_j05,
    ),
    Task(
        id="j06",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, using all "
            "orders regardless of status (no status filtering). For each tier, count the "
            "number of orders. Return exactly two columns: tier, n_orders. Row order does "
            "not matter."
        ),
        inputs=("orders", "customers"),
        reference=_j06,
    ),
    Task(
        id="j07",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id and to the "
            'products table on product_id, then keep only rows where status equals '
            '"paid". For each combination of region (customers with a null region form '
            "their own null-region group) and category, compute revenue = sum(qty * "
            "unit_price). Return exactly three columns: region, category, revenue. Row "
            "order does not matter."
        ),
        inputs=("orders", "customers", "products"),
        reference=_j07,
    ),
    Task(
        id="j08",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, then keep only "
            'rows where status equals "paid". Compute order_value = qty * unit_price for '
            "each such order (before discount), then for each region compute "
            "avg_order_value = mean(order_value) rounded to 2 decimal places. Return "
            "exactly two columns: region, avg_order_value. Row order does not matter."
        ),
        inputs=("orders", "customers"),
        reference=_j08,
    ),
)
