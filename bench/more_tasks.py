"""70 additional benchmark tasks (Task 23), growing the suite to >= 100.

Same conventions as ``bench/seed_tasks.py``: every reference function takes a
mapping of dataset name -> polars.DataFrame (as produced by
``bench.datasets.load_fixtures``) and returns the expected output DataFrame,
matching the exact semantics stated in the task's prompt. Ids continue from
the seed suite: s11.., m13.., j09..
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping

import polars as pl

from bench.tasks import Task

# ---------------------------------------------------------------------------
# single-table tasks (s11..s33)
# ---------------------------------------------------------------------------


def _s11(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.filter((pl.col("status") == "paid") & (pl.col("qty") >= 3))


def _s12(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.filter((pl.col("status") == "pending") | (pl.col("status") == "cancelled"))


def _s13(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.filter(~(pl.col("status") == "paid"))


def _s14(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(
        pl.col("order_id"),
        pl.col("unit_price").floor().alias("floor_price"),
        pl.col("unit_price").ceil().alias("ceil_price"),
    )


def _s15(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(pl.col("order_id"), (pl.col("qty") - 5).abs().alias("qty_diff"))


def _s16(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(pl.col("order_id"), pl.col("unit_price").sqrt().alias("price_sqrt"))


def _s17(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(pl.col("order_id"), pl.col("unit_price").log().alias("price_log"))


def _s18(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(pl.col("order_id"), pl.col("qty").clip(1, 10).alias("qty_clipped"))


def _s19(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    return customers.select(
        pl.col("customer_id"), pl.col("name").str.to_lowercase().alias("name_lower")
    )


def _s20(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    return customers.select(
        pl.col("customer_id"), pl.col("region").is_null().alias("region_missing")
    )


def _s21(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    return customers.select(
        pl.col("customer_id"), pl.col("region").fill_null("unknown").alias("region_filled")
    )


def _s22(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    products = frames["products"]
    return products.filter(pl.col("product_name").str.contains("o", literal=True))


def _s23(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    products = frames["products"]
    return products.filter(pl.col("product_name").str.starts_with("N"))


def _s24(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    products = frames["products"]
    return products.select(
        pl.col("product_id"),
        pl.col("product_name").str.replace_all(" ", "_", literal=True).alias("name_us"),
    )


def _s25(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    products = frames["products"]
    return products.select(
        pl.col("product_id"),
        (pl.col("category") + pl.col("product_name")).alias("label"),
    )


def _s26(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(
        pl.col("order_id"), pl.col("unit_price").cast(pl.Int64).alias("unit_price_int")
    )


def _s27(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(pl.col("order_id"), pl.col("qty").cast(pl.Float64).alias("qty_float"))


def _s28(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(
        pl.col("order_id"),
        (pl.col("qty").cast(pl.Utf8) + pl.lit(" units")).alias("qty_label"),
    )


def _s29(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(
        pl.col("order_id"),
        pl.col("order_date").dt.month().alias("order_month"),
        pl.col("order_date").dt.day().alias("order_day"),
    )


def _s30(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    cutoff = datetime.date(2024, 7, 1)
    return orders.filter(pl.col("order_date") >= cutoff)


def _s31(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    return (
        customers.select("customer_id", "signup_date")
        .sort(["signup_date", "customer_id"], descending=False)
        .head(5)
    )


def _s32(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.group_by("status").agg(
        pl.col("unit_price").min().alias("min_price"),
        pl.col("unit_price").max().alias("max_price"),
    )


def _s33(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return orders.select(pl.col("qty").sum().alias("total_qty"), pl.len().alias("n_orders"))


# ---------------------------------------------------------------------------
# multi-step tasks (m13..m40)
# ---------------------------------------------------------------------------


def _m13(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    products = frames["products"]
    cost_tier = (
        pl.when(pl.col("cost") < 10)
        .then(pl.lit("low"))
        .when(pl.col("cost") < 30)
        .then(pl.lit("medium"))
        .otherwise(pl.lit("high"))
        .alias("cost_tier")
    )
    return products.with_columns(cost_tier).group_by("cost_tier").agg(pl.len().alias("n_products"))


def _m14(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    price_tier = (
        pl.when(pl.col("unit_price") >= 100)
        .then(pl.lit("premium"))
        .when(pl.col("unit_price") >= 50)
        .then(pl.lit("standard"))
        .when(pl.col("unit_price") >= 20)
        .then(pl.lit("budget"))
        .otherwise(pl.lit("clearance"))
        .alias("price_tier")
    )
    return orders.with_columns(price_tier).group_by("price_tier").agg(pl.len().alias("n_orders"))


def _m15(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns(pl.col("discount").fill_null(0.0).alias("effective_discount"))
        .group_by("status")
        .agg(pl.col("effective_discount").mean().alias("mean_discount"))
    )


def _m16(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns(pl.col("discount").is_null().alias("discount_missing"))
        .group_by(["status", "discount_missing"])
        .agg(pl.len().alias("n_orders"))
    )


def _m17(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.filter(pl.col("status") == "paid")
        .select("order_id", "qty")
        .sort(["qty", "order_id"], descending=True)
        .head(5)
    )


def _m18(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    stripped = pl.col("name").str.strip_chars()
    return (
        customers.with_columns(stripped.alias("name_stripped"))
        .filter(pl.col("name_stripped").str.starts_with("A"))
        .group_by("region")
        .agg(pl.len().alias("n_customers"))
    )


def _m19(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.filter(pl.col("order_date").dt.year() == 2023)
        .with_columns(pl.col("order_date").dt.month().alias("month"))
        .group_by("month")
        .agg(pl.col("qty").sum().alias("total_qty"))
    )


def _m20(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns(pl.col("unit_price").round(1).alias("rounded_price"))
        .group_by("status")
        .agg(pl.col("rounded_price").mean().round(2).alias("mean_rounded_price"))
    )


def _m21(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns(pl.col("qty").clip(1, 8).alias("qty_clipped"))
        .group_by("product_id")
        .agg(pl.col("qty_clipped").sum().alias("total_clipped_qty"))
    )


def _m22(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    cutoff = datetime.date(2024, 6, 1)
    return (
        orders.with_columns((pl.col("order_date") + pl.duration(days=14)).alias("due_date"))
        .filter(pl.col("due_date") > cutoff)
        .group_by("status")
        .agg(pl.len().alias("n_orders"))
    )


def _m23(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    reference_date = datetime.date(2025, 1, 1)
    return (
        orders.with_columns(
            (pl.lit(reference_date) - pl.col("order_date")).dt.total_days().alias("days_old")
        )
        .filter(pl.col("days_old") <= 365)
        .group_by("status")
        .agg(pl.len().alias("n_orders"))
    )


def _m24(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns(pl.col("qty").cast(pl.Utf8).alias("qty_str"))
        .filter(pl.col("qty_str").str.contains("1", literal=True))
        .group_by("status")
        .agg(pl.len().alias("n_orders"))
    )


def _m25(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.group_by("customer_id")
        .agg(pl.len().alias("n_orders"))
        .sort(["n_orders", "customer_id"], descending=False)
        .head(5)
    )


def _m26(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    paid = orders.filter(pl.col("status") == "paid")
    return paid.select(
        (pl.col("qty") * pl.col("unit_price")).sum().alias("total_revenue"),
        pl.col("unit_price").mean().round(2).alias("avg_unit_price"),
    )


def _m27(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    return (
        customers.with_columns(
            pl.col("tier").str.replace_all("gold", "premium", literal=True).alias("tier_label")
        )
        .group_by("tier_label")
        .agg(pl.len().alias("n_customers"))
    )


def _m28(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    region_tier = (pl.col("region").fill_null("na") + pl.col("tier")).alias("region_tier")
    return (
        customers.with_columns(region_tier)
        .group_by("region_tier")
        .agg(pl.len().alias("n_customers"))
    )


def _m29(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    discount_level = (
        pl.when(pl.col("discount").is_null() | (pl.col("discount") == 0))
        .then(pl.lit("none"))
        .when(pl.col("discount") < 0.15)
        .then(pl.lit("low"))
        .otherwise(pl.lit("high"))
        .alias("discount_level")
    )
    return (
        orders.with_columns(discount_level)
        .group_by("discount_level")
        .agg(pl.len().alias("n_orders"))
    )


def _m30(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    return (
        customers.with_columns(pl.col("signup_date").dt.month().alias("signup_month"))
        .group_by(["tier", "signup_month"])
        .agg(pl.len().alias("n_customers"))
    )


def _m31(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    products = frames["products"]
    return (
        products.filter(pl.col("cost") >= 10)
        .select("product_id", "product_name", "cost")
        .sort(["cost", "product_id"], descending=True)
        .head(5)
    )


def _m32(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"].rename({"unit_price": "price"})
    return orders.select(pl.col("order_id"), (pl.col("qty") * pl.col("price")).alias("revenue"))


def _m33(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    return customers.select(
        "customer_id",
        "name",
        "tier",
        pl.col("name").str.len_chars().cast(pl.Int64).alias("name_len"),
    )


def _m34(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns(pl.col("unit_price").ceil().alias("ceil_price"))
        .filter(pl.col("ceil_price") >= 50)
        .group_by("status")
        .agg(pl.len().alias("n_orders"))
    )


def _m35(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns(pl.col("qty").sqrt().alias("sqrt_qty"))
        .filter(pl.col("sqrt_qty") >= 2)
        .group_by("status")
        .agg(pl.len().alias("n_orders"))
    )


def _m36(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns((pl.col("unit_price") - 50).abs().alias("price_dev"))
        .group_by("status")
        .agg(pl.col("price_dev").mean().round(2).alias("mean_price_dev"))
    )


def _m37(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    return (
        orders.with_columns(pl.col("unit_price").log().alias("log_price"))
        .filter(pl.col("log_price") >= 3)
        .group_by("status")
        .agg(pl.len().alias("n_orders"))
    )


def _m38(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"]
    reference_date = datetime.date(2025, 1, 1)
    return (
        customers.with_columns(
            (pl.lit(reference_date) - pl.col("signup_date")).dt.total_days().alias("days_since")
        )
        .filter(pl.col("days_since") > 500)
        .group_by("tier")
        .agg(pl.len().alias("n_customers"))
    )


def _m39(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    products = frames["products"]
    return (
        products.with_columns(
            pl.col("product_name").str.replace_all("a", "A", literal=True).alias("clean_name")
        )
        .filter(pl.col("clean_name").str.contains("A", literal=True))
        .group_by("category")
        .agg(pl.len().alias("n_products"))
    )


def _m40(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    grouped = orders.group_by("product_id").agg(
        pl.len().alias("total"),
        (pl.col("status") == "pending").sum().cast(pl.Int64).alias("pending_n"),
    )
    return grouped.with_columns(
        (pl.col("pending_n") / pl.col("total")).alias("pending_rate")
    ).select("product_id", "pending_rate")


# ---------------------------------------------------------------------------
# join tasks (j09..j27)
# ---------------------------------------------------------------------------


def _j09(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.join(products.select("product_id", "category"), on="product_id", how="inner")
    return joined.group_by("category").agg(
        pl.col("unit_price").mean().round(2).alias("avg_unit_price")
    )


def _j10(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.join(customers.select("customer_id", "tier"), on="customer_id", how="inner")
    return (
        joined.filter(pl.col("status") == "paid")
        .group_by("tier")
        .agg((pl.col("qty") * pl.col("unit_price")).sum().alias("total_revenue"))
    )


def _j11(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.join(products, on="product_id", how="left")
    margin = ((pl.col("unit_price") - pl.col("cost")) * pl.col("qty")).alias("margin")
    return (
        joined.with_columns(margin)
        .filter(pl.col("margin") > 0)
        .group_by("category")
        .agg(pl.len().alias("n_orders"))
    )


def _j12(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.join(customers.select("customer_id", "region"), on="customer_id", how="left")
    net = (pl.col("qty") * pl.col("unit_price") * (1 - pl.col("discount").fill_null(0.0))).alias(
        "net"
    )
    return joined.with_columns(net).group_by("region").agg(pl.col("net").sum().alias("net_revenue"))


def _j13(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.filter(pl.col("status") == "paid").join(products, on="product_id", how="left")
    margin = ((pl.col("unit_price") - pl.col("cost")) * pl.col("qty")).alias("margin")
    return joined.with_columns(margin).select(pl.col("margin").sum().alias("total_margin"))


def _j14(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.filter(pl.col("status") == "paid").join(
        customers.select("customer_id", "region"), on="customer_id", how="left"
    )
    return (
        joined.with_columns((pl.col("qty") * pl.col("unit_price")).alias("order_value"))
        .select("order_id", "customer_id", "region", "order_value")
        .sort(["order_value", "order_id"], descending=True)
        .head(5)
    )


def _j15(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.filter(pl.col("status") == "paid").join(products, on="product_id", how="left")
    margin = ((pl.col("unit_price") - pl.col("cost")) * pl.col("qty")).alias("margin")
    margin_level = (
        pl.when(pl.col("margin") >= 20)
        .then(pl.lit("high"))
        .when(pl.col("margin") >= 5)
        .then(pl.lit("medium"))
        .otherwise(pl.lit("low"))
        .alias("margin_level")
    )
    return (
        joined.with_columns(margin)
        .with_columns(margin_level)
        .group_by("margin_level")
        .agg(pl.len().alias("n_orders"))
    )


def _j16(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.join(customers.select("customer_id", "region"), on="customer_id", how="left")
    region_label = pl.col("region").fill_null("unknown").str.to_uppercase().alias("region_label")
    return (
        joined.with_columns(region_label).group_by("region_label").agg(pl.len().alias("n_orders"))
    )


def _j17(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.join(products.select("product_id", "category"), on="product_id", how="left")
    return (
        joined.filter(pl.col("order_date").dt.year() == 2024)
        .group_by("category")
        .agg(pl.len().alias("n_orders"), pl.col("qty").sum().alias("total_qty"))
    )


def _j18(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.join(customers.select("customer_id", "tier"), on="customer_id", how="left")
    rounded_value = (pl.col("qty") * pl.col("unit_price")).round(1).alias("rounded_value")
    return (
        joined.with_columns(rounded_value)
        .group_by("tier")
        .agg(pl.col("rounded_value").mean().round(2).alias("mean_order_value"))
    )


def _j19(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    paid_counts = (
        orders.filter(pl.col("status") == "paid")
        .group_by("customer_id")
        .agg(pl.len().alias("n_paid_orders"))
    )
    joined = customers.join(paid_counts, on="customer_id", how="left")
    return joined.filter(pl.col("n_paid_orders").is_null()).select("customer_id", "name")


def _j20(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    products = frames["products"]
    joined = (
        orders.filter(pl.col("status") == "paid")
        .join(customers.select("customer_id", "region"), on="customer_id", how="left")
        .join(products.select("product_id", "category"), on="product_id", how="left")
    )
    return joined.group_by(["region", "category"]).agg(pl.len().alias("n_orders"))


def _j21(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.join(products.select("product_id", "category"), on="product_id", how="left")
    category_label = (pl.col("category") + pl.lit(" goods")).alias("category_label")
    return (
        joined.with_columns(category_label)
        .group_by("category_label")
        .agg(pl.col("qty").sum().alias("total_qty"))
    )


def _j22(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.join(customers.select("customer_id", "region"), on="customer_id", how="left")
    return (
        joined.filter(pl.col("status") == "refunded")
        .group_by("region")
        .agg(pl.len().alias("n_orders"))
    )


def _j23(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.filter(pl.col("status") == "paid").join(
        products.select("product_id", "product_name"), on="product_id", how="left"
    )
    return (
        joined.group_by(["product_id", "product_name"])
        .agg(
            pl.col("unit_price").min().alias("min_price"),
            pl.col("unit_price").max().alias("max_price"),
        )
        .select("product_name", "min_price", "max_price")
    )


def _j24(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.join(
        products.select("product_id", "product_name", "cost"), on="product_id", how="inner"
    )
    cost_ratio = (pl.col("cost") / pl.col("unit_price")).alias("cost_ratio")
    return (
        joined.with_columns(cost_ratio)
        .select("order_id", "product_name", "cost_ratio")
        .sort(["cost_ratio", "order_id"], descending=True)
        .head(10)
    )


def _j25(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    joined = orders.join(customers.select("customer_id", "region"), on="customer_id", how="left")
    region_group = (
        pl.when(pl.col("region").is_null())
        .then(pl.lit("unspecified"))
        .otherwise(pl.col("region"))
        .alias("region_group")
    )
    return (
        joined.with_columns(region_group).group_by("region_group").agg(pl.len().alias("n_orders"))
    )


def _j26(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    products = frames["products"]
    joined = orders.filter((pl.col("status") == "paid") & (pl.col("qty") >= 1)).join(
        products, on="product_id", how="left"
    )
    profit = ((pl.col("unit_price") - pl.col("cost")) * pl.col("qty")).alias("profit")
    return (
        joined.with_columns(profit)
        .group_by("category")
        .agg(
            pl.col("profit").sum().alias("total_profit"),
            pl.col("unit_price").mean().round(2).alias("avg_unit_price"),
        )
    )


def _j27(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    customers = frames["customers"]
    products = frames["products"]
    joined = (
        orders.filter(pl.col("status") == "paid")
        .join(customers.select("customer_id", "region"), on="customer_id", how="left")
        .join(products.select("product_id", "category", "cost"), on="product_id", how="left")
    )
    margin = ((pl.col("unit_price") - pl.col("cost")) * pl.col("qty")).alias("margin")
    return (
        joined.with_columns(margin)
        .select("order_id", "region", "category", "margin")
        .sort(["margin", "order_id"], descending=True)
        .head(5)
    )


MORE_TASKS: tuple[Task, ...] = (
    Task(
        id="s11",
        tier="single",
        prompt=(
            'Using the orders table, keep only rows where status equals "paid" and qty is '
            "greater than or equal to 3 (both conditions must hold). Return all order "
            "columns unchanged (order_id, customer_id, product_id, qty, unit_price, status, "
            "order_date, discount). Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s11,
    ),
    Task(
        id="s12",
        tier="single",
        prompt=(
            'Using the orders table, keep rows where status equals "pending" or status '
            'equals "cancelled" (either condition is enough). Return all order columns '
            "unchanged (order_id, customer_id, product_id, qty, unit_price, status, "
            "order_date, discount). Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s12,
    ),
    Task(
        id="s13",
        tier="single",
        prompt=(
            'Using the orders table, keep only rows where status does NOT equal "paid". '
            "Return all order columns unchanged (order_id, customer_id, product_id, qty, "
            "unit_price, status, order_date, discount). Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s13,
    ),
    Task(
        id="s14",
        tier="single",
        prompt=(
            "Using the orders table, compute floor_price = unit_price rounded down to the "
            "nearest whole number, and ceil_price = unit_price rounded up to the nearest "
            "whole number, for every row. Return exactly three columns: order_id, "
            "floor_price, ceil_price. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s14,
    ),
    Task(
        id="s15",
        tier="single",
        prompt=(
            "Using the orders table, compute qty_diff = the absolute value of (qty - 5) "
            "for every row. Return exactly two columns: order_id, qty_diff. Row order does "
            "not matter."
        ),
        inputs=("orders",),
        reference=_s15,
    ),
    Task(
        id="s16",
        tier="single",
        prompt=(
            "Using the orders table, compute price_sqrt = the square root of unit_price "
            "for every row. Return exactly two columns: order_id, price_sqrt. Row order "
            "does not matter."
        ),
        inputs=("orders",),
        reference=_s16,
    ),
    Task(
        id="s17",
        tier="single",
        prompt=(
            "Using the orders table, compute price_log = the natural logarithm of "
            "unit_price for every row. Return exactly two columns: order_id, price_log. "
            "Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s17,
    ),
    Task(
        id="s18",
        tier="single",
        prompt=(
            "Using the orders table, compute qty_clipped = qty clamped to the range 1 to "
            "10 inclusive (values below 1 become 1, values above 10 become 10). Return "
            "exactly two columns: order_id, qty_clipped. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s18,
    ),
    Task(
        id="s19",
        tier="single",
        prompt=(
            "Using the customers table, compute name_lower = the raw (not stripped) name "
            "converted to lowercase. Return exactly two columns: customer_id, name_lower. "
            "Row order does not matter."
        ),
        inputs=("customers",),
        reference=_s19,
    ),
    Task(
        id="s20",
        tier="single",
        prompt=(
            "Using the customers table, compute region_missing = true when region is null, "
            "false otherwise. Return exactly two columns: customer_id, region_missing. Row "
            "order does not matter."
        ),
        inputs=("customers",),
        reference=_s20,
    ),
    Task(
        id="s21",
        tier="single",
        prompt=(
            'Using the customers table, compute region_filled = region, or "unknown" when '
            "region is null. Return exactly two columns: customer_id, region_filled. Row "
            "order does not matter."
        ),
        inputs=("customers",),
        reference=_s21,
    ),
    Task(
        id="s22",
        tier="single",
        prompt=(
            "Using the products table, keep only rows where product_name contains the "
            'letter "o" (lowercase, anywhere in the name). Return all product columns '
            "unchanged (product_id, product_name, category, cost). Row order does not "
            "matter."
        ),
        inputs=("products",),
        reference=_s22,
    ),
    Task(
        id="s23",
        tier="single",
        prompt=(
            "Using the products table, keep only rows where product_name starts with the "
            'letter "N". Return all product columns unchanged (product_id, product_name, '
            "category, cost). Row order does not matter."
        ),
        inputs=("products",),
        reference=_s23,
    ),
    Task(
        id="s24",
        tier="single",
        prompt=(
            "Using the products table, compute name_us = product_name with every space "
            "character replaced by an underscore. Return exactly two columns: product_id, "
            "name_us. Row order does not matter."
        ),
        inputs=("products",),
        reference=_s24,
    ),
    Task(
        id="s25",
        tier="single",
        prompt=(
            "Using the products table, compute label = category concatenated directly with "
            "product_name (no separator). Return exactly two columns: product_id, label. "
            "Row order does not matter."
        ),
        inputs=("products",),
        reference=_s25,
    ),
    Task(
        id="s26",
        tier="single",
        prompt=(
            "Using the orders table, compute unit_price_int = unit_price converted to an "
            "integer by truncating toward zero (dropping the fractional part). Return "
            "exactly two columns: order_id, unit_price_int. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s26,
    ),
    Task(
        id="s27",
        tier="single",
        prompt=(
            "Using the orders table, compute qty_float = qty converted to a floating-point "
            "number. Return exactly two columns: order_id, qty_float. Row order does not "
            "matter."
        ),
        inputs=("orders",),
        reference=_s27,
    ),
    Task(
        id="s28",
        tier="single",
        prompt=(
            "Using the orders table, compute qty_label = qty converted to text, "
            'concatenated with the literal text " units" (e.g. qty 3 becomes "3 units"). '
            "Return exactly two columns: order_id, qty_label. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s28,
    ),
    Task(
        id="s29",
        tier="single",
        prompt=(
            "Using the orders table, extract order_month (calendar month, 1-12) and "
            "order_day (day of month) from order_date for every row. Return exactly three "
            "columns: order_id, order_month, order_day. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_s29,
    ),
    Task(
        id="s30",
        tier="single",
        prompt=(
            "Using the orders table, keep only rows where order_date falls on or after "
            "2024-07-01. Return all order columns unchanged (order_id, customer_id, "
            "product_id, qty, unit_price, status, order_date, discount). Row order does "
            "not matter."
        ),
        inputs=("orders",),
        reference=_s30,
    ),
    Task(
        id="s31",
        tier="single",
        prompt=(
            "Using the customers table, find the 5 customers with the earliest "
            "signup_date. Break ties by customer_id ascending (lower customer_id first). "
            "Return exactly two columns: customer_id, signup_date, sorted by signup_date "
            "ascending (and by customer_id ascending among ties). Row order matters and "
            "must match this sort exactly."
        ),
        inputs=("customers",),
        reference=_s31,
        ordered=True,
    ),
    Task(
        id="s32",
        tier="single",
        prompt=(
            "Using the orders table, compute the minimum and maximum unit_price for each "
            "status value. Return exactly three columns: status, min_price, max_price. Row "
            "order does not matter."
        ),
        inputs=("orders",),
        reference=_s32,
    ),
    Task(
        id="s33",
        tier="single",
        prompt=(
            "Using the orders table (all rows, no grouping), compute total_qty = the sum "
            "of qty across every order, and n_orders = the total count of orders. Return "
            "exactly two columns: total_qty, n_orders (a single row). Row order does not "
            "matter."
        ),
        inputs=("orders",),
        reference=_s33,
    ),
    Task(
        id="m13",
        tier="multi",
        prompt=(
            'Using the products table, classify every product into a cost_tier: "low" if '
            'cost is less than 10, otherwise "medium" if cost is less than 30, otherwise '
            '"high". Count the number of products in each cost_tier. Return exactly two '
            "columns: cost_tier, n_products. Row order does not matter."
        ),
        inputs=("products",),
        reference=_m13,
    ),
    Task(
        id="m14",
        tier="multi",
        prompt=(
            "Using the orders table, classify every order into a price_tier by unit_price: "
            '"premium" if unit_price >= 100, otherwise "standard" if unit_price >= 50, '
            'otherwise "budget" if unit_price >= 20, otherwise "clearance". Count the '
            "number of orders in each price_tier. Return exactly two columns: price_tier, "
            "n_orders. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m14,
    ),
    Task(
        id="m15",
        tier="multi",
        prompt=(
            "Using the orders table, treat a missing (null) discount as 0 for every row. "
            "For each status value, compute mean_discount = the mean of that treated "
            "discount. Return exactly two columns: status, mean_discount. Row order does "
            "not matter."
        ),
        inputs=("orders",),
        reference=_m15,
    ),
    Task(
        id="m16",
        tier="multi",
        prompt=(
            "Using the orders table, compute discount_missing = true when discount is "
            "null, false otherwise. For each combination of status and discount_missing, "
            "count the number of orders. Return exactly three columns: status, "
            "discount_missing, n_orders. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m16,
    ),
    Task(
        id="m17",
        tier="multi",
        prompt=(
            'Using the orders table, keep only rows where status equals "paid", then find '
            "the 5 highest qty values among them. Break ties by order_id descending "
            "(higher order_id first). Return exactly two columns: order_id, qty, sorted by "
            "qty descending (and by order_id descending among ties). Row order matters and "
            "must match this sort exactly."
        ),
        inputs=("orders",),
        reference=_m17,
        ordered=True,
    ),
    Task(
        id="m18",
        tier="multi",
        prompt=(
            "Using the customers table, strip leading and trailing whitespace from name, "
            'then keep only customers whose stripped name starts with the letter "A". '
            "Count the number of such customers in each region (customers with a null "
            "region form their own null-region group). Return exactly two columns: "
            "region, n_customers. Row order does not matter."
        ),
        inputs=("customers",),
        reference=_m18,
    ),
    Task(
        id="m19",
        tier="multi",
        prompt=(
            "Using the orders table, keep only rows where order_date falls in calendar "
            "year 2023. Extract the calendar month (1-12) from order_date and compute "
            "total_qty = sum(qty) per month. Return exactly two columns: month, total_qty. "
            "Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m19,
    ),
    Task(
        id="m20",
        tier="multi",
        prompt=(
            "Using the orders table, round unit_price to 1 decimal place for every row. "
            "For each status, compute mean_rounded_price = the mean of that rounded value, "
            "rounded to 2 decimal places. Return exactly two columns: status, "
            "mean_rounded_price. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m20,
    ),
    Task(
        id="m21",
        tier="multi",
        prompt=(
            "Using the orders table, compute qty_clipped = qty clamped to the range 1 to "
            "8 inclusive. For each product_id, compute total_clipped_qty = the sum of "
            "qty_clipped. Return exactly two columns: product_id, total_clipped_qty. Row "
            "order does not matter."
        ),
        inputs=("orders",),
        reference=_m21,
    ),
    Task(
        id="m22",
        tier="multi",
        prompt=(
            "Using the orders table, compute due_date = order_date plus 14 days. Keep "
            "only rows where due_date is after 2024-06-01. Count the number of remaining "
            "orders for each status value. Return exactly two columns: status, n_orders. "
            "Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m22,
    ),
    Task(
        id="m23",
        tier="multi",
        prompt=(
            "Using the orders table, compute days_old = the number of days from "
            "order_date to 2025-01-01. Keep only rows where days_old is less than or "
            "equal to 365. Count the number of remaining orders for each status value. "
            "Return exactly two columns: status, n_orders. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m23,
    ),
    Task(
        id="m24",
        tier="multi",
        prompt=(
            "Using the orders table, convert qty to text as qty_str. Keep only rows where "
            'qty_str contains the character "1". Count the number of remaining orders for '
            "each status value. Return exactly two columns: status, n_orders. Row order "
            "does not matter."
        ),
        inputs=("orders",),
        reference=_m24,
    ),
    Task(
        id="m25",
        tier="multi",
        prompt=(
            "Using the orders table, count n_orders = the number of orders for each "
            "customer_id. Find the 5 customers with the fewest orders. Break ties by "
            "customer_id ascending (lower customer_id first). Return exactly two columns: "
            "customer_id, n_orders, sorted by n_orders ascending (and by customer_id "
            "ascending among ties). Row order matters and must match this sort exactly."
        ),
        inputs=("orders",),
        reference=_m25,
        ordered=True,
    ),
    Task(
        id="m26",
        tier="multi",
        prompt=(
            'Using the orders table, keep only rows where status equals "paid". Over '
            "those rows (all rows together, no grouping), compute total_revenue = "
            "sum(qty * unit_price) and avg_unit_price = mean(unit_price) rounded to 2 "
            "decimal places. Return exactly two columns: total_revenue, avg_unit_price (a "
            "single row). Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m26,
    ),
    Task(
        id="m27",
        tier="multi",
        prompt=(
            "Using the customers table, compute tier_label = tier with every occurrence of "
            '"gold" replaced by "premium" (other tier values are unchanged). Count the '
            "number of customers for each tier_label. Return exactly two columns: "
            "tier_label, n_customers. Row order does not matter."
        ),
        inputs=("customers",),
        reference=_m27,
    ),
    Task(
        id="m28",
        tier="multi",
        prompt=(
            'Using the customers table, compute region_tier = region (or "na" when region '
            "is null) concatenated directly with tier (no separator). Count the number of "
            "customers for each region_tier. Return exactly two columns: region_tier, "
            "n_customers. Row order does not matter."
        ),
        inputs=("customers",),
        reference=_m28,
    ),
    Task(
        id="m29",
        tier="multi",
        prompt=(
            'Using the orders table, compute discount_level for every order: "none" if '
            'discount is null or equal to 0, otherwise "low" if discount is less than '
            '0.15, otherwise "high". Count the number of orders for each discount_level. '
            "Return exactly two columns: discount_level, n_orders. Row order does not "
            "matter."
        ),
        inputs=("orders",),
        reference=_m29,
    ),
    Task(
        id="m30",
        tier="multi",
        prompt=(
            "Using the customers table, extract the calendar month (1-12) from "
            "signup_date as signup_month. For each combination of tier and signup_month, "
            "count the number of customers. Return exactly three columns: tier, "
            "signup_month, n_customers. Row order does not matter."
        ),
        inputs=("customers",),
        reference=_m30,
    ),
    Task(
        id="m31",
        tier="multi",
        prompt=(
            "Using the products table, keep only rows where cost is greater than or equal "
            "to 10, then find the 5 highest-cost products among them. Break ties by "
            "product_id descending (higher product_id first). Return exactly three "
            "columns: product_id, product_name, cost, sorted by cost descending (and by "
            "product_id descending among ties). Row order matters and must match this "
            "sort exactly."
        ),
        inputs=("products",),
        reference=_m31,
        ordered=True,
    ),
    Task(
        id="m32",
        tier="multi",
        prompt=(
            "Using the orders table, rename the unit_price column to price, then compute "
            "revenue = qty * price. Return exactly two columns: order_id, revenue. Row "
            "order does not matter."
        ),
        inputs=("orders",),
        reference=_m32,
    ),
    Task(
        id="m33",
        tier="multi",
        prompt=(
            "Using the customers table, select customer_id, name, and tier, and compute "
            "name_len = the character length of the raw (not stripped) name. Return "
            "exactly four columns: customer_id, name, tier, name_len. Row order does not "
            "matter."
        ),
        inputs=("customers",),
        reference=_m33,
    ),
    Task(
        id="m34",
        tier="multi",
        prompt=(
            "Using the orders table, compute ceil_price = unit_price rounded up to the "
            "nearest whole number. Keep only rows where ceil_price is greater than or "
            "equal to 50. Count the number of remaining orders for each status value. "
            "Return exactly two columns: status, n_orders. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m34,
    ),
    Task(
        id="m35",
        tier="multi",
        prompt=(
            "Using the orders table, compute sqrt_qty = the square root of qty. Keep only "
            "rows where sqrt_qty is greater than or equal to 2. Count the number of "
            "remaining orders for each status value. Return exactly two columns: status, "
            "n_orders. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m35,
    ),
    Task(
        id="m36",
        tier="multi",
        prompt=(
            "Using the orders table, compute price_dev = the absolute value of "
            "(unit_price - 50). For each status, compute mean_price_dev = the mean of "
            "price_dev, rounded to 2 decimal places. Return exactly two columns: status, "
            "mean_price_dev. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m36,
    ),
    Task(
        id="m37",
        tier="multi",
        prompt=(
            "Using the orders table, compute log_price = the natural logarithm of "
            "unit_price. Keep only rows where log_price is greater than or equal to 3. "
            "Count the number of remaining orders for each status value. Return exactly "
            "two columns: status, n_orders. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m37,
    ),
    Task(
        id="m38",
        tier="multi",
        prompt=(
            "Using the customers table, compute days_since = the number of days from "
            "signup_date to 2025-01-01. Keep only rows where days_since is greater than "
            "500. Count the number of remaining customers for each tier value. Return "
            "exactly two columns: tier, n_customers. Row order does not matter."
        ),
        inputs=("customers",),
        reference=_m38,
    ),
    Task(
        id="m39",
        tier="multi",
        prompt=(
            "Using the products table, compute clean_name = product_name with every "
            'lowercase "a" replaced by uppercase "A". Keep only rows where clean_name '
            'contains "A". Count the number of remaining products for each category. '
            "Return exactly two columns: category, n_products. Row order does not matter."
        ),
        inputs=("products",),
        reference=_m39,
    ),
    Task(
        id="m40",
        tier="multi",
        prompt=(
            "Using the orders table (all statuses, no filtering), for each product_id "
            'compute pending_rate = (count of orders with status equal to "pending") / '
            "(total count of orders) for that product, as a float. Only products that "
            "appear in the orders table are included. Return exactly two columns: "
            "product_id, pending_rate. Row order does not matter."
        ),
        inputs=("orders",),
        reference=_m40,
    ),
    Task(
        id="j09",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, keeping only "
            "orders whose product_id matches a product (an inner join; every order in "
            "this dataset has a matching product). For each category compute "
            "avg_unit_price = mean(unit_price) rounded to 2 decimal places. Return exactly "
            "two columns: category, avg_unit_price. Row order does not matter."
        ),
        inputs=("orders", "products"),
        reference=_j09,
    ),
    Task(
        id="j10",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, keeping only "
            "orders whose customer_id matches a customer (an inner join; every order in "
            "this dataset has a matching customer), then keep only rows where status "
            'equals "paid". For each tier compute total_revenue = sum(qty * '
            "unit_price). Return exactly two columns: tier, total_revenue. Row order does "
            "not matter."
        ),
        inputs=("orders", "customers"),
        reference=_j10,
    ),
    Task(
        id="j11",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, using all orders "
            "regardless of status (no status filtering). Compute margin = (unit_price - "
            "cost) * qty for each order, then keep only rows where margin is greater than "
            "0. Count the number of remaining orders for each category. Return exactly "
            "two columns: category, n_orders. Row order does not matter."
        ),
        inputs=("orders", "products"),
        reference=_j11,
    ),
    Task(
        id="j12",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, using all "
            "orders regardless of status. Compute net = qty * unit_price * (1 - discount), "
            "treating a missing (null) discount as 0. For each region (customers with a "
            "null region form their own null-region group) compute net_revenue = sum(net). "
            "Return exactly two columns: region, net_revenue. Row order does not matter."
        ),
        inputs=("orders", "customers"),
        reference=_j12,
    ),
    Task(
        id="j13",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, then keep only "
            'rows where status equals "paid". Compute margin = (unit_price - cost) * qty '
            "for each order. Over all such orders together (no grouping), compute "
            "total_margin = sum(margin). Return exactly one column: total_margin (a single "
            "row). Row order does not matter."
        ),
        inputs=("orders", "products"),
        reference=_j13,
    ),
    Task(
        id="j14",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, then keep only "
            'rows where status equals "paid". Compute order_value = qty * unit_price. '
            "Find the 5 orders with the highest order_value. Break ties by order_id "
            "descending (higher order_id first). Return exactly four columns: order_id, "
            "customer_id, region, order_value, sorted by order_value descending (and by "
            "order_id descending among ties). Row order matters and must match this sort "
            "exactly."
        ),
        inputs=("orders", "customers"),
        reference=_j14,
        ordered=True,
    ),
    Task(
        id="j15",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, then keep only "
            'rows where status equals "paid". Compute margin = (unit_price - cost) * qty, '
            'then classify margin_level: "high" if margin >= 20, otherwise "medium" if '
            'margin >= 5, otherwise "low". Count the number of orders for each '
            "margin_level. Return exactly two columns: margin_level, n_orders. Row order "
            "does not matter."
        ),
        inputs=("orders", "products"),
        reference=_j15,
    ),
    Task(
        id="j16",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, using all "
            'orders regardless of status. Compute region_label = region (or "unknown" '
            "when region is null) converted to uppercase. Count the number of orders for "
            "each region_label. Return exactly two columns: region_label, n_orders. Row "
            "order does not matter."
        ),
        inputs=("orders", "customers"),
        reference=_j16,
    ),
    Task(
        id="j17",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, using all orders "
            "regardless of status, then keep only rows where order_date falls in calendar "
            "year 2024. For each category compute n_orders = count of orders and "
            "total_qty = sum(qty). Return exactly three columns: category, n_orders, "
            "total_qty. Row order does not matter."
        ),
        inputs=("orders", "products"),
        reference=_j17,
    ),
    Task(
        id="j18",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, using all "
            "orders regardless of status. Compute rounded_value = (qty * unit_price) "
            "rounded to 1 decimal place. For each tier compute mean_order_value = the "
            "mean of rounded_value, rounded to 2 decimal places. Return exactly two "
            "columns: tier, mean_order_value. Row order does not matter."
        ),
        inputs=("orders", "customers"),
        reference=_j18,
    ),
    Task(
        id="j19",
        tier="join",
        prompt=(
            'Using the orders table, keep only rows where status equals "paid" and count '
            "n_paid_orders for each customer_id. Join the customers table to this per-"
            "customer count on customer_id, keeping every customer (customers with zero "
            "paid orders get a missing count). Return the customers whose n_paid_orders is "
            "missing: exactly two columns, customer_id, name (the raw, unstripped name). "
            "Row order does not matter."
        ),
        inputs=("orders", "customers"),
        reference=_j19,
    ),
    Task(
        id="j20",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id and to the "
            "products table on product_id, then keep only rows where status equals "
            '"paid". For each combination of region (customers with a null region form '
            "their own null-region group) and category, count the number of orders. "
            "Return exactly three columns: region, category, n_orders. Row order does not "
            "matter."
        ),
        inputs=("orders", "customers", "products"),
        reference=_j20,
    ),
    Task(
        id="j21",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, using all orders "
            "regardless of status. Compute category_label = category concatenated with "
            'the literal text " goods". For each category_label compute total_qty = '
            "sum(qty). Return exactly two columns: category_label, total_qty. Row order "
            "does not matter."
        ),
        inputs=("orders", "products"),
        reference=_j21,
    ),
    Task(
        id="j22",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, then keep only "
            'rows where status equals "refunded". For each region (customers with a null '
            "region form their own null-region group) count the number of orders. Return "
            "exactly two columns: region, n_orders. Row order does not matter."
        ),
        inputs=("orders", "customers"),
        reference=_j22,
    ),
    Task(
        id="j23",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, then keep only "
            'rows where status equals "paid". For each product compute min_price = '
            "min(unit_price) and max_price = max(unit_price) among its paid orders. "
            "Return exactly three columns: product_name, min_price, max_price. Row order "
            "does not matter."
        ),
        inputs=("orders", "products"),
        reference=_j23,
    ),
    Task(
        id="j24",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, keeping only "
            "orders whose product_id matches a product (an inner join; every order in "
            "this dataset has a matching product). Compute cost_ratio = cost / "
            "unit_price for each order. Find the 10 orders with the highest cost_ratio. "
            "Break ties by order_id descending (higher order_id first). Return exactly "
            "three columns: order_id, product_name, cost_ratio, sorted by cost_ratio "
            "descending (and by order_id descending among ties). Row order matters and "
            "must match this sort exactly."
        ),
        inputs=("orders", "products"),
        reference=_j24,
        ordered=True,
    ),
    Task(
        id="j25",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id, using all "
            'orders regardless of status. Compute region_group = region, or "unspecified" '
            "when region is null. Count the number of orders for each region_group. "
            "Return exactly two columns: region_group, n_orders. Row order does not "
            "matter."
        ),
        inputs=("orders", "customers"),
        reference=_j25,
    ),
    Task(
        id="j26",
        tier="join",
        prompt=(
            "Join the orders table to the products table on product_id, then keep only "
            'rows where status equals "paid" and qty is greater than or equal to 1. '
            "Compute profit = (unit_price - cost) * qty for each order. For each category "
            "compute total_profit = sum(profit) and avg_unit_price = mean(unit_price) "
            "rounded to 2 decimal places. Return exactly three columns: category, "
            "total_profit, avg_unit_price. Row order does not matter."
        ),
        inputs=("orders", "products"),
        reference=_j26,
    ),
    Task(
        id="j27",
        tier="join",
        prompt=(
            "Join the orders table to the customers table on customer_id and to the "
            "products table on product_id, then keep only rows where status equals "
            '"paid". Compute margin = (unit_price - cost) * qty for each order. Find the '
            "5 orders with the highest margin. Break ties by order_id descending (higher "
            "order_id first). Return exactly four columns: order_id, region, category, "
            "margin, sorted by margin descending (and by order_id descending among ties). "
            "Row order matters and must match this sort exactly."
        ),
        inputs=("orders", "customers", "products"),
        reference=_j27,
        ordered=True,
    ),
)
