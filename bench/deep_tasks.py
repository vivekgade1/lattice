"""30 "deep" benchmark tasks (d01..d30): 5-8 dependent transformation steps each.

The seed/more suites saturate for a strong model (plain pandas scores ~100%), so
this tier asks for genuinely multi-stage pipelines where later steps consume
earlier *intermediate* results: aggregates of aggregates, ratios derived from
aggregates and joined back to row- or dimension-level tables, share-of-total via
a whole-table total, left joins whose missing aggregates must be coalesced,
multi-branch buckets computed on aggregated values, cohort/date logic, and
string normalization before grouping. Prompts describe the desired result the
way an analyst would; they never spell out the step sequence.

Same conventions as ``bench/seed_tasks.py``: every reference takes a mapping of
dataset name -> polars.DataFrame (as produced by ``bench.datasets.load_fixtures``)
and returns the expected output. Every task has a hand-written, checked Lattice
solution in ``tests/fixtures/lattice_solutions/dNN.py``.

Shared vocabulary used by the prompts:
- "paid" means status == "paid";
- an order's revenue is qty * unit_price;
- an order's net revenue is qty * unit_price * (1 - discount), a missing discount
  counting as 0;
- an order's margin is qty * (unit_price - cost), cost from the products table.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping

import polars as pl

from bench.tasks import Task

_PAID = pl.col("status") == "paid"
_REVENUE = pl.col("qty") * pl.col("unit_price")
_NET = pl.col("qty") * pl.col("unit_price") * (1 - pl.col("discount").fill_null(0.0))


def _paid(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    return frames["orders"].filter(_PAID)


def _d01(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    by_cat = (
        _paid(frames)
        .join(frames["products"], on="product_id")
        .group_by("category")
        .agg(_REVENUE.sum().alias("raw"))
    )
    total = by_cat["raw"].sum()
    return by_cat.select(
        "category",
        pl.col("raw").round(2).alias("paid_revenue"),
        (pl.col("raw") / total).round(4).alias("revenue_share"),
    )


def _d02(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    per_customer = _paid(frames).group_by("customer_id").agg(_REVENUE.sum().alias("rev"))
    return (
        per_customer.join(frames["customers"], on="customer_id")
        .with_columns(pl.col("region").fill_null("unknown").alias("region_label"))
        .group_by("region_label")
        .agg(pl.len().alias("n_paying_customers"), pl.col("rev").sum().alias("total"))
        .select(
            "region_label",
            "n_paying_customers",
            (pl.col("total") / pl.col("n_paying_customers"))
            .round(2)
            .alias("avg_revenue_per_customer"),
        )
    )


def _d03(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    per_customer = _paid(frames).group_by("customer_id").agg(_REVENUE.sum().alias("rev"))
    rev = pl.col("rev").fill_null(0.0)
    bucket = (
        pl.when(rev == 0)
        .then(pl.lit("none"))
        .when(rev < 1500)
        .then(pl.lit("low"))
        .when(rev < 4000)
        .then(pl.lit("mid"))
        .otherwise(pl.lit("high"))
    )
    return (
        frames["customers"]
        .join(per_customer, on="customer_id", how="left")
        .with_columns(bucket.alias("value_bucket"))
        .group_by("tier", "value_bucket")
        .agg(pl.len().alias("n_customers"))
    )


def _d04(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = _paid(frames).filter(pl.col("order_date").dt.year() == 2024)
    return (
        orders.join(frames["customers"], on="customer_id")
        .with_columns(
            pl.col("order_date").dt.month().cast(pl.Int64).alias("order_month"),
            (pl.col("signup_date").dt.year() == 2024).alias("is_new"),
        )
        .group_by("order_month")
        .agg(
            pl.len().alias("n_orders"),
            pl.col("is_new").cast(pl.Int64).sum().alias("n_new"),
        )
        .select(
            "order_month",
            "n_orders",
            (pl.col("n_new") / pl.col("n_orders")).round(3).alias("new_customer_share"),
        )
        .sort("order_month")
    )


def _d05(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    per_customer = (
        _paid(frames)
        .group_by("customer_id")
        .agg(_REVENUE.sum().alias("rev"))
        .join(frames["customers"], on="customer_id")
        .filter(pl.col("region").is_not_null())
    )
    region_avg = per_customer.group_by("region").agg(pl.col("rev").mean().alias("avg"))
    return (
        per_customer.join(region_avg, on="region")
        .filter(pl.col("rev") > pl.col("avg"))
        .select(
            "customer_id",
            "region",
            pl.col("rev").round(2).alias("paid_revenue"),
            pl.col("avg").round(2).alias("region_avg_revenue"),
        )
    )


def _d06(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    counts = _paid(frames).group_by("customer_id").agg(pl.len().alias("n"))
    return (
        frames["customers"]
        .join(counts, on="customer_id", how="left")
        .with_columns(
            pl.col("name").str.strip_chars().str.to_lowercase().alias("name_key"),
            pl.col("n").fill_null(0).alias("n0"),
        )
        .group_by("name_key")
        .agg(pl.len().alias("n_customers"), pl.col("n0").sum().alias("n_paid_orders"))
        .filter(pl.col("n_customers") >= 2)
    )


def _d07(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    return (
        _paid(frames)
        .join(frames["products"], on="product_id")
        .group_by("product_id", "product_name")
        .agg(
            pl.len().alias("n"),
            (pl.col("qty") * (pl.col("unit_price") - pl.col("cost"))).sum().alias("margin"),
            _REVENUE.sum().alias("rev"),
        )
        .filter(pl.col("n") >= 10)
        .sort("margin", descending=True)
        .head(5)
        .select(
            "product_id",
            "product_name",
            pl.col("margin").round(2).alias("paid_margin"),
            (100 * pl.col("margin") / pl.col("rev")).round(1).alias("margin_pct"),
        )
    )


def _d08(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    lost = pl.col("status").is_in(["cancelled", "refunded"])
    per_year = (
        frames["orders"]
        .join(frames["products"], on="product_id")
        .with_columns(
            pl.col("order_date").dt.year().cast(pl.Int64).alias("order_year"),
            lost.cast(pl.Int64).alias("lost"),
        )
        .group_by("category", "order_year")
        .agg(pl.len().alias("n"), pl.col("lost").sum().alias("n_lost"))
    )
    overall = per_year.group_by("category").agg(
        pl.col("n").sum().alias("tn"), pl.col("n_lost").sum().alias("tl")
    )
    return (
        per_year.join(overall, on="category")
        .filter(pl.col("n_lost") / pl.col("n") > pl.col("tl") / pl.col("tn"))
        .select(
            "category",
            "order_year",
            (pl.col("n_lost") / pl.col("n")).round(3).alias("year_loss_rate"),
            (pl.col("tl") / pl.col("tn")).round(3).alias("overall_loss_rate"),
        )
    )


def _d09(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    first = _paid(frames).group_by("customer_id").agg(pl.col("order_date").min().alias("first"))
    return (
        first.join(frames["customers"], on="customer_id")
        .with_columns(
            (pl.col("first") - pl.col("signup_date")).dt.total_days().alias("days"),
            pl.col("signup_date").dt.year().cast(pl.Int64).alias("signup_year"),
        )
        .filter(pl.col("days") >= 0)
        .group_by("signup_year")
        .agg(pl.len().alias("n_customers"), pl.col("days").mean().alias("avg"))
        .select(
            "signup_year",
            "n_customers",
            pl.col("avg").round(1).alias("avg_days_to_first_order"),
        )
    )


def _d10(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    year = pl.col("order_date").dt.year()
    return (
        _paid(frames)
        .with_columns(pl.col("order_date").dt.month().cast(pl.Int64).alias("order_month"))
        .group_by("order_month")
        .agg(
            pl.when(year == 2023).then(_REVENUE).otherwise(0.0).sum().alias("r23"),
            pl.when(year == 2024).then(_REVENUE).otherwise(0.0).sum().alias("r24"),
        )
        .select(
            "order_month",
            pl.col("r23").round(2).alias("revenue_2023"),
            pl.col("r24").round(2).alias("revenue_2024"),
            (100 * (pl.col("r24") - pl.col("r23")) / pl.col("r23")).round(1).alias("growth_pct"),
        )
        .sort("order_month")
    )


def _d11(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    per_cat = (
        _paid(frames)
        .join(frames["products"], on="product_id")
        .group_by("customer_id", "category")
        .agg(pl.col("qty").sum().alias("category_qty"))
    )
    best = per_cat.group_by("customer_id").agg(pl.col("category_qty").max().alias("m"))
    return (
        per_cat.join(best, on="customer_id")
        .filter(pl.col("category_qty") == pl.col("m"))
        .select("customer_id", "category", "category_qty")
    )


def _d12(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    by_tier = (
        _paid(frames)
        .join(frames["customers"], on="customer_id")
        .group_by("tier")
        .agg(_NET.sum().alias("net"), pl.len().alias("n"))
    )
    total = by_tier["net"].sum()
    return by_tier.select(
        "tier",
        pl.col("net").round(2).alias("net_revenue"),
        (pl.col("net") / total).round(4).alias("revenue_share"),
        (pl.col("net") / pl.col("n")).round(2).alias("avg_order_net"),
    )


def _discount_bucket() -> pl.Expr:
    d = pl.col("discount")
    return (
        pl.when(d.is_null())
        .then(pl.lit("none"))
        .when(d < 0.1)
        .then(pl.lit("small"))
        .when(d < 0.2)
        .then(pl.lit("medium"))
        .otherwise(pl.lit("large"))
    )


def _d13(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    paid = _paid(frames)
    overall = paid["qty"].mean()
    return (
        paid.with_columns(_discount_bucket().alias("discount_bucket"))
        .group_by("discount_bucket")
        .agg(pl.col("qty").mean().alias("avg"), _NET.sum().alias("net"))
        .filter(pl.col("avg") > overall)
        .select(
            "discount_bucket",
            pl.col("avg").round(2).alias("avg_qty"),
            pl.col("net").round(2).alias("net_revenue"),
        )
    )


def _d14(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    cells = (
        _paid(frames)
        .join(frames["customers"], on="customer_id")
        .join(frames["products"], on="product_id")
        .filter(pl.col("region").is_not_null())
        .group_by("region", "category")
        .agg(_REVENUE.sum().alias("rev"))
    )
    totals = cells.group_by("region").agg(pl.col("rev").sum().alias("total"))
    return cells.join(totals, on="region").select(
        "region",
        "category",
        pl.col("rev").round(2).alias("paid_revenue"),
        (pl.col("rev") / pl.col("total")).round(4).alias("region_share"),
    )


def _d15(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    repeats = (
        _paid(frames)
        .group_by("customer_id", "product_id")
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") >= 2)
        .group_by("customer_id")
        .agg(pl.len().alias("repeat_products"))
    )
    return (
        repeats.join(frames["customers"], on="customer_id")
        .select(
            "customer_id",
            pl.col("name").str.strip_chars().alias("name_clean"),
            "repeat_products",
        )
        .sort(["repeat_products", "customer_id"], descending=True)
    )


def _d16(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    low = pl.col("unit_price") < 1.5 * pl.col("cost")
    return (
        _paid(frames)
        .join(frames["products"], on="product_id")
        .group_by("category")
        .agg(pl.len().alias("n_paid_orders"), low.cast(pl.Int64).sum().alias("n_low_markup"))
        .select(
            "category",
            "n_paid_orders",
            "n_low_markup",
            (pl.col("n_low_markup") / pl.col("n_paid_orders")).round(3).alias("low_markup_share"),
        )
    )


def _d17(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    year = pl.col("order_date").dt.year()
    per_customer = (
        _paid(frames)
        .group_by("customer_id")
        .agg(
            (year == 2023).cast(pl.Int64).sum().alias("paid_orders_2023"),
            (year == 2024).cast(pl.Int64).sum().alias("n24"),
        )
        .filter((pl.col("paid_orders_2023") > 0) & (pl.col("n24") == 0))
    )
    return per_customer.join(frames["customers"], on="customer_id").select(
        "customer_id",
        pl.col("name").str.strip_chars().alias("name_clean"),
        "paid_orders_2023",
    )


def _d18(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    month = pl.col("order_date").dt.month()
    quarter = (
        pl.when(month <= 3)
        .then(1)
        .when(month <= 6)
        .then(2)
        .when(month <= 9)
        .then(3)
        .otherwise(4)
        .cast(pl.Int64)
    )
    cells = (
        _paid(frames)
        .with_columns(
            pl.col("order_date").dt.year().cast(pl.Int64).alias("order_year"),
            quarter.alias("quarter"),
        )
        .group_by("order_year", "quarter")
        .agg(_REVENUE.sum().alias("rev"))
    )
    totals = cells.group_by("order_year").agg(pl.col("rev").sum().alias("total"))
    return (
        cells.join(totals, on="order_year")
        .select(
            "order_year",
            "quarter",
            pl.col("rev").round(2).alias("revenue"),
            (pl.col("rev") / pl.col("total")).round(4).alias("year_share"),
        )
        .sort(["order_year", "quarter"])
    )


def _d19(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    gold_qty = pl.when(pl.col("tier") == "gold").then(pl.col("qty")).otherwise(0)
    per_product = (
        _paid(frames)
        .join(frames["customers"], on="customer_id")
        .group_by("product_id")
        .agg(gold_qty.sum().alias("g"), pl.col("qty").sum().alias("t"))
        .with_columns((pl.col("g") / pl.col("t")).alias("share"))
        .filter(pl.col("share") >= 0.4)
    )
    return per_product.join(frames["products"], on="product_id").select(
        "product_id", "product_name", pl.col("share").round(3).alias("gold_qty_share")
    )


_AS_OF = datetime.date(2025, 1, 1)


def _d20(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    per_customer = (
        _paid(frames).group_by("customer_id").agg(_REVENUE.sum().alias("rev"), pl.len().alias("n"))
    )
    tenure = (pl.lit(_AS_OF) - pl.col("signup_date")).dt.total_days()
    bucket = (
        pl.when(tenure < 365)
        .then(pl.lit("under_1y"))
        .when(tenure < 730)
        .then(pl.lit("1_to_2y"))
        .otherwise(pl.lit("over_2y"))
    )
    return (
        frames["customers"]
        .join(per_customer, on="customer_id", how="left")
        .with_columns(
            bucket.alias("tenure_bucket"),
            pl.col("rev").fill_null(0.0).alias("rev0"),
            pl.col("n").is_not_null().cast(pl.Int64).alias("active"),
        )
        .group_by("tenure_bucket")
        .agg(
            pl.len().alias("n_customers"),
            pl.col("active").sum().alias("n_active"),
            pl.col("rev0").sum().alias("total"),
        )
        .select(
            "tenure_bucket",
            "n_customers",
            (pl.col("n_active") / pl.col("n_customers")).round(3).alias("active_share"),
            (pl.col("total") / pl.col("n_customers")).round(2).alias("avg_paid_revenue"),
        )
    )


def _d21(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    per_product = (
        _paid(frames)
        .group_by("product_id")
        .agg(pl.col("qty").sum().alias("paid_qty"))
        .join(frames["products"], on="product_id")
    )
    cat_avg = per_product.group_by("category").agg(pl.col("paid_qty").mean().alias("avg"))
    return (
        per_product.join(cat_avg, on="category")
        .filter(pl.col("paid_qty") > pl.col("avg"))
        .select(
            "category",
            "product_id",
            "paid_qty",
            (pl.col("paid_qty") / pl.col("avg")).round(2).alias("qty_vs_category_avg"),
        )
    )


def _d22(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    customers = frames["customers"].with_columns(
        pl.col("signup_date").dt.year().cast(pl.Int64).alias("signup_year")
    )
    cohorts = customers.group_by("signup_year").agg(pl.len().alias("cohort_size"))
    next_year = (
        frames["orders"]
        .join(customers, on="customer_id")
        .filter(_PAID & (pl.col("order_date").dt.year() == pl.col("signup_year") + 1))
        .group_by("signup_year")
        .agg(pl.len().alias("n"))
    )
    return (
        cohorts.join(next_year, on="signup_year", how="left")
        .with_columns(pl.col("n").fill_null(0).alias("next_year_paid_orders"))
        .select(
            "signup_year",
            "cohort_size",
            "next_year_paid_orders",
            (pl.col("next_year_paid_orders") / pl.col("cohort_size"))
            .round(3)
            .alias("orders_per_customer"),
        )
    )


def _d23(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    paid = _paid(frames).with_columns(_REVENUE.alias("rev"))
    avg = paid.group_by("customer_id").agg(pl.col("rev").mean().alias("avg"))
    return (
        paid.join(avg, on="customer_id")
        .filter(pl.col("rev") > 2 * pl.col("avg"))
        .select(
            "order_id",
            "customer_id",
            (pl.col("rev") / pl.col("avg")).round(2).alias("spike_ratio"),
        )
        .sort(["spike_ratio", "order_id"], descending=True)
    )


def _d24(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    def tier_qty(tier: str) -> pl.Expr:
        return pl.when(pl.col("tier") == tier).then(pl.col("qty")).otherwise(0).sum()

    return (
        _paid(frames)
        .join(frames["customers"], on="customer_id")
        .join(frames["products"], on="product_id")
        .group_by("category")
        .agg(
            tier_qty("gold").alias("gold_qty"),
            tier_qty("silver").alias("silver_qty"),
            tier_qty("bronze").alias("bronze_qty"),
            pl.col("qty").sum().alias("t"),
        )
        .select(
            "category",
            "gold_qty",
            "silver_qty",
            "bronze_qty",
            (pl.col("gold_qty") / pl.col("t")).round(3).alias("gold_qty_share"),
        )
    )


def _d25(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    name = pl.col("product_name").str.to_lowercase()
    furniture = (
        name.str.contains("chair", literal=True)
        | name.str.contains("desk", literal=True)
        | name.str.contains("shelf", literal=True)
        | name.str.contains("lamp", literal=True)
    )
    line = pl.when(furniture).then(pl.lit("furniture")).otherwise(pl.lit("gear"))
    return (
        _paid(frames)
        .join(frames["products"], on="product_id")
        .with_columns(line.alias("product_line"))
        .group_by("product_line")
        .agg(
            pl.len().alias("n_paid_orders"),
            _REVENUE.sum().alias("rev"),
            (pl.col("unit_price") / pl.col("cost")).mean().alias("markup"),
        )
        .select(
            "product_line",
            "n_paid_orders",
            pl.col("rev").round(2).alias("paid_revenue"),
            pl.col("markup").round(3).alias("avg_markup"),
        )
    )


def _d26(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    points = (_REVENUE / 10).floor().cast(pl.Int64) * pl.when(pl.col("tier") == "gold").then(
        2
    ).otherwise(1)
    per_customer = (
        _paid(frames)
        .join(frames["customers"], on="customer_id")
        .with_columns(
            points.alias("points"),
            pl.col("region").fill_null("unknown").alias("region_label"),
        )
        .group_by("customer_id", "region_label")
        .agg(pl.col("points").sum().alias("customer_points"))
        .filter(pl.col("customer_points") >= 400)
    )
    return per_customer.group_by("region_label").agg(
        pl.len().alias("n_loyal_customers"),
        pl.col("customer_points").sum().alias("total_points"),
    )


_PENDING_AS_OF = datetime.date(2024, 12, 31)


def _d27(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    orders = frames["orders"]
    age = (pl.lit(_PENDING_AS_OF) - pl.col("order_date")).dt.total_days()
    pending = (
        orders.filter(pl.col("status") == "pending")
        .group_by("customer_id")
        .agg(_REVENUE.sum().alias("pv"), age.max().alias("oldest_pending_days"))
    )
    paid = _paid(frames).group_by("customer_id").agg(_REVENUE.sum().alias("rev"))
    return (
        pending.join(paid, on="customer_id", how="left")
        .with_columns(pl.col("rev").fill_null(0.0).alias("rev0"))
        .filter(pl.col("pv") > pl.col("rev0"))
        .select(
            "customer_id",
            pl.col("pv").round(2).alias("pending_value"),
            pl.col("rev0").round(2).alias("paid_revenue"),
            "oldest_pending_days",
        )
    )


def _d28(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    year = pl.col("order_date").dt.year()
    r23, r24 = pl.col("r23"), pl.col("r24")
    trend = (
        pl.when(r24 > 1.1 * r23)
        .then(pl.lit("growing"))
        .when(r24 < 0.9 * r23)
        .then(pl.lit("shrinking"))
        .otherwise(pl.lit("flat"))
    )
    return (
        _paid(frames)
        .join(frames["products"], on="product_id")
        .group_by("category")
        .agg(
            pl.when(year == 2023).then(_REVENUE).otherwise(0.0).sum().alias("r23"),
            pl.when(year == 2024).then(_REVENUE).otherwise(0.0).sum().alias("r24"),
        )
        .select(
            "category",
            r23.round(2).alias("revenue_2023"),
            r24.round(2).alias("revenue_2024"),
            trend.alias("trend"),
        )
    )


def _d29(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    counts = _paid(frames).group_by("customer_id").agg(pl.len().alias("n"))
    return (
        frames["customers"]
        .join(counts, on="customer_id", how="left")
        .with_columns(pl.col("n").fill_null(0).cast(pl.Int64).alias("paid_orders"))
        .group_by("paid_orders")
        .agg(pl.len().alias("n_customers"))
        .sort("paid_orders")
    )


def _d30(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    per_customer = _paid(frames).group_by("customer_id").agg(_NET.sum().alias("net"))
    return (
        frames["customers"]
        .join(per_customer, on="customer_id", how="left")
        .filter(pl.col("region").is_not_null())
        .group_by("region", "tier")
        .agg(
            pl.len().alias("n_customers"),
            pl.col("net").fill_null(0.0).sum().alias("segment_net_revenue"),
        )
        .with_columns(
            (pl.col("segment_net_revenue") / pl.col("n_customers"))
            .round(2)
            .alias("avg_net_per_customer")
        )
        .sort(["avg_net_per_customer", "n_customers"], descending=True)
        .head(5)
    )


_DEFS = "Definitions: 'paid' means status == \"paid\"; an order's revenue is qty * unit_price"
_NET_DEF = (
    "an order's net revenue is qty * unit_price * (1 - discount), with a missing discount "
    "counting as 0"
)
_MARGIN_DEF = "an order's margin is qty * (unit_price - cost), using the product's cost"


def _t(
    task_id: str,
    prompt: str,
    inputs: tuple[str, ...],
    reference,  # noqa: ANN001 - Callable[[Mapping[str, pl.DataFrame]], pl.DataFrame]
    ordered: bool = False,
) -> Task:
    return Task(
        id=task_id, tier="deep", prompt=prompt, inputs=inputs, reference=reference, ordered=ordered
    )


DEEP_TASKS: tuple[Task, ...] = (
    _t(
        "d01",
        f"{_DEFS}. Using the orders and products tables, report how paid revenue splits across "
        "product categories: for each category, its total paid revenue and that category's "
        "share of the total paid revenue over all categories. Return exactly three columns: "
        "category, paid_revenue (rounded to 2 decimals), revenue_share (a fraction between 0 "
        "and 1, rounded to 4 decimals, computed from unrounded totals). Row order does not "
        "matter.",
        ("orders", "products"),
        _d01,
    ),
    _t(
        "d02",
        f"{_DEFS}. Using the orders and customers tables, consider only customers with at least "
        "one paid order ('paying customers'). For each customer region — customers whose region "
        'is missing are grouped under the label "unknown" — report the number of paying '
        "customers and the average, over those customers, of each customer's total paid "
        "revenue. Return exactly three columns: region_label, n_paying_customers, "
        "avg_revenue_per_customer (rounded to 2 decimals). Row order does not matter.",
        ("orders", "customers"),
        _d02,
    ),
    _t(
        "d03",
        f"{_DEFS}. Using the orders and customers tables, classify every customer (including "
        "customers with no paid orders) by their total paid revenue into a value_bucket: "
        '"none" if it is 0 (or they have no paid orders), otherwise "low" if below 1500, '
        '"mid" if below 4000, otherwise "high". Then count customers for each combination of '
        "customer tier and value_bucket (only combinations that occur). Return exactly three "
        "columns: tier, value_bucket, n_customers. Row order does not matter.",
        ("orders", "customers"),
        _d03,
    ),
    _t(
        "d04",
        f"{_DEFS}. Using the orders and customers tables, look at paid orders placed in 2024. "
        "For each calendar month of the order date, report the number of such orders and the "
        "share of them placed by customers who signed up in 2024 (signup_date year == 2024). "
        "Return exactly three columns: order_month (1-12), n_orders, new_customer_share (a "
        "fraction, rounded to 3 decimals). Sort rows by order_month ascending.",
        ("orders", "customers"),
        _d04,
        ordered=True,
    ),
    _t(
        "d05",
        f"{_DEFS}. Using the orders and customers tables, find the customers whose total paid "
        "revenue is strictly greater than the average total paid revenue of the paying "
        "customers in their own region (a paying customer has at least one paid order; the "
        "regional average is over paying customers in that region only). Customers with a "
        "missing region are excluded entirely. Compare unrounded values. Return exactly four "
        "columns: customer_id, region, paid_revenue (rounded to 2 decimals), "
        "region_avg_revenue (rounded to 2 decimals). Row order does not matter.",
        ("orders", "customers"),
        _d05,
    ),
    _t(
        "d06",
        f"{_DEFS}. Using the customers and orders tables: some customer names differ only by "
        "surrounding whitespace or letter case. Normalize each customer's name by trimming "
        "leading/trailing whitespace and lowercasing it, and find normalized names shared by "
        "two or more customers. For each such name, report how many customers carry it and the "
        "total number of paid orders placed by those customers (customers without paid orders "
        "contribute 0). Return exactly three columns: name_key (the normalized name), "
        "n_customers, n_paid_orders. Row order does not matter.",
        ("customers", "orders"),
        _d06,
    ),
    _t(
        "d07",
        f"{_DEFS}; {_MARGIN_DEF}. Using the orders and products tables, among products with "
        "at least 10 paid orders, list the 5 with the highest total paid margin. For each, "
        "report the total paid margin and the margin as a percentage of the product's total "
        "paid revenue (100 * margin / revenue). Return exactly four columns: product_id, "
        "product_name, paid_margin (rounded to 2 decimals), margin_pct (rounded to 1 "
        "decimal). Sort rows by paid_margin descending (the unrounded margins are all "
        "distinct).",
        ("orders", "products"),
        _d07,
        ordered=True,
    ),
    _t(
        "d08",
        "Using the orders and products tables, an order is 'lost' if its status is "
        '"cancelled" or "refunded". Consider all orders regardless of status. For each '
        "product category and order year, compute the loss rate (lost orders / all orders). "
        "Report only the category-years whose loss rate is strictly higher than that "
        "category's overall loss rate across all years (lost orders / all orders in the "
        "category). Compare unrounded values. Return exactly four columns: category, "
        "order_year, year_loss_rate, overall_loss_rate (both rounded to 3 decimals). Row "
        "order does not matter.",
        ("orders", "products"),
        _d08,
    ),
    _t(
        "d09",
        f"{_DEFS}. Using the orders and customers tables, for each customer with at least one "
        "paid order, compute the number of days from their signup_date to the date of their "
        "earliest paid order (earliest order_date minus signup_date, in days). Ignore customers "
        "for whom this is negative. Then, for each signup year, report the number of such "
        "customers and the mean of these day counts. Return exactly three columns: "
        "signup_year, n_customers, avg_days_to_first_order (rounded to 1 decimal). Row order "
        "does not matter.",
        ("orders", "customers"),
        _d09,
    ),
    _t(
        "d10",
        f"{_DEFS}. Using the orders table, compare paid revenue month by month between 2023 and "
        "2024: for each calendar month number of the order date (1-12), report total paid "
        "revenue in 2023, total paid revenue in 2024, and the growth percentage "
        "100 * (revenue_2024 - revenue_2023) / revenue_2023 computed from unrounded totals. "
        "Return exactly four columns: order_month, revenue_2023, revenue_2024 (both rounded to "
        "2 decimals), growth_pct (rounded to 1 decimal). Sort rows by order_month ascending.",
        ("orders",),
        _d10,
        ordered=True,
    ),
    _t(
        "d11",
        f"{_DEFS}. Using the orders and products tables, find each customer's favourite product "
        "category: the category in which the customer bought the largest total paid quantity "
        "(sum of qty over paid orders). If several categories tie for a customer's largest "
        "quantity, report every tied category. Only customers with paid orders appear. Return "
        "exactly three columns: customer_id, category, category_qty. Row order does not "
        "matter.",
        ("orders", "products"),
        _d11,
    ),
    _t(
        "d12",
        f"{_DEFS}; {_NET_DEF}. Using the orders and customers tables, for each customer tier "
        "report total paid net revenue, that tier's share of paid net revenue across all "
        "tiers, and the average net revenue per paid order in the tier. Return exactly four "
        "columns: tier, net_revenue (rounded to 2 decimals), revenue_share (a fraction, "
        "rounded to 4 decimals), avg_order_net (rounded to 2 decimals); compute everything from "
        "unrounded values. Row order does not matter.",
        ("orders", "customers"),
        _d12,
    ),
    _t(
        "d13",
        f"{_DEFS}; {_NET_DEF}. Using the orders table, put each paid order in a "
        'discount_bucket: "none" if discount is missing, else "small" if discount < 0.1, '
        '"medium" if discount < 0.2, otherwise "large". Report the buckets whose average qty '
        "is strictly greater than the average qty over all paid orders (compare unrounded "
        "values). For each reported bucket give its average qty and total paid net revenue. "
        "Return exactly three columns: discount_bucket, avg_qty (rounded to 2 decimals), "
        "net_revenue (rounded to 2 decimals). Row order does not matter.",
        ("orders",),
        _d13,
    ),
    _t(
        "d14",
        f"{_DEFS}. Using the orders, customers and products tables, for each customer region "
        "and product category, report total paid revenue and the share of that region's total "
        "paid revenue that the category represents. Orders from customers with a missing "
        "region are excluded. Return exactly four columns: region, category, paid_revenue "
        "(rounded to 2 decimals), region_share (a fraction, rounded to 4 decimals, from "
        "unrounded values). Only region/category pairs with paid orders appear. Row order does "
        "not matter.",
        ("orders", "customers", "products"),
        _d14,
    ),
    _t(
        "d15",
        f"{_DEFS}. Using the orders and customers tables, a customer 'repeat-buys' a product "
        "if they have two or more paid orders for that product_id. For every customer who "
        "repeat-buys at least one product, report how many distinct products they repeat-buy "
        "and their name with leading/trailing whitespace removed. Return exactly three "
        "columns: customer_id, name_clean, repeat_products. Sort rows by repeat_products "
        "descending, breaking ties by customer_id descending.",
        ("orders", "customers"),
        _d15,
        ordered=True,
    ),
    _t(
        "d16",
        f"{_DEFS}. Using the orders and products tables, a paid order is 'low markup' when its "
        "unit_price is below 1.5 times the product's cost. For each product category report "
        "the number of paid orders, the number of low-markup paid orders, and the low-markup "
        "share (low-markup / paid orders). Return exactly four columns: category, "
        "n_paid_orders, n_low_markup, low_markup_share (rounded to 3 decimals). Row order does "
        "not matter.",
        ("orders", "products"),
        _d16,
    ),
    _t(
        "d17",
        f"{_DEFS}. Using the orders and customers tables, find lapsed customers: customers "
        "who had at least one paid order dated in 2023 but no paid order dated in 2024. For "
        "each, report their number of paid orders in 2023 and their name with "
        "leading/trailing whitespace removed. Return exactly three columns: customer_id, "
        "name_clean, paid_orders_2023. Row order does not matter.",
        ("orders", "customers"),
        _d17,
    ),
    _t(
        "d18",
        f"{_DEFS}. Using the orders table, report paid revenue per calendar quarter (quarter 1 "
        "= months 1-3, 2 = months 4-6, 3 = months 7-9, 4 = months 10-12) of each order year, "
        "together with the share of that year's total paid revenue falling in the quarter. "
        "Return exactly four columns: order_year, quarter, revenue (rounded to 2 decimals), "
        "year_share (a fraction, rounded to 4 decimals, from unrounded values). Sort rows by "
        "order_year ascending, then quarter ascending.",
        ("orders",),
        _d18,
        ordered=True,
    ),
    _t(
        "d19",
        f"{_DEFS}. Using the orders, customers and products tables, for each product compute "
        "the share of its total paid quantity (sum of qty over paid orders) that was bought by "
        'customers whose tier is "gold". Report products whose unrounded gold share is at '
        "least 0.4. Return exactly three columns: product_id, product_name, gold_qty_share "
        "(rounded to 3 decimals). Row order does not matter.",
        ("orders", "customers", "products"),
        _d19,
    ),
    _t(
        "d20",
        f"{_DEFS}. Using the customers and orders tables, compute each customer's tenure as of "
        '2025-01-01 (days from signup_date to 2025-01-01) and bucket it: "under_1y" if '
        'below 365 days, "1_to_2y" if below 730 days, otherwise "over_2y". For each '
        "tenure_bucket report the number of customers, the share of them with at least one "
        "paid order, and the average total paid revenue per customer, where customers "
        "without paid orders count as 0. Return exactly four columns: tenure_bucket, "
        "n_customers, active_share (rounded to 3 decimals), avg_paid_revenue (rounded to 2 "
        "decimals). Row order does not matter.",
        ("customers", "orders"),
        _d20,
    ),
    _t(
        "d21",
        f"{_DEFS}. Using the orders and products tables, compute each product's total paid "
        "quantity (sum of qty over paid orders; only products with paid orders count). Report "
        "the products whose paid quantity is strictly greater than the average paid quantity "
        "of the products in the same category, with the ratio of the two. Return exactly four "
        "columns: category, product_id, paid_qty, qty_vs_category_avg (paid_qty divided by the "
        "category average, rounded to 2 decimals). Row order does not matter.",
        ("orders", "products"),
        _d21,
    ),
    _t(
        "d22",
        f"{_DEFS}. Using the customers and orders tables, group customers into cohorts by "
        "signup year. For each cohort, report the cohort size (all customers who signed up "
        "that year), the number of paid orders placed by cohort members in the calendar year "
        "right after their signup year (order year == signup year + 1), and that number "
        "divided by the cohort size. Cohorts with no such orders report 0. Return exactly four "
        "columns: signup_year, cohort_size, next_year_paid_orders, orders_per_customer "
        "(rounded to 3 decimals). Row order does not matter.",
        ("customers", "orders"),
        _d22,
    ),
    _t(
        "d23",
        f"{_DEFS}. Using the orders table, find 'spike' paid orders: paid orders whose revenue "
        "is more than twice the average revenue of that customer's paid orders. For each, "
        "report spike_ratio = order revenue / the customer's average paid order revenue. "
        "Return exactly three columns: order_id, customer_id, spike_ratio (rounded to 2 "
        "decimals). Sort rows by spike_ratio descending (the rounded value), breaking ties by "
        "order_id descending.",
        ("orders",),
        _d23,
        ordered=True,
    ),
    _t(
        "d24",
        f"{_DEFS}. Using the orders, customers and products tables, break down paid quantity "
        "(sum of qty over paid orders) for each product category by customer tier, one column "
        "per tier, plus the gold tier's share of the category's paid quantity. Return exactly "
        "five columns: category, gold_qty, silver_qty, bronze_qty, gold_qty_share (rounded "
        "to 3 decimals). Row order does not matter.",
        ("orders", "customers", "products"),
        _d24,
    ),
    _t(
        "d25",
        f"{_DEFS}. Using the orders and products tables, assign each product a product_line: "
        '"furniture" if its product_name, lowercased, contains any of "chair", "desk", '
        '"shelf" or "lamp", otherwise "gear". For each product_line report the number of paid '
        "orders, their total revenue, and the mean over those paid orders of the markup "
        "unit_price / cost. Return exactly four columns: product_line, n_paid_orders, "
        "paid_revenue (rounded to 2 decimals), avg_markup (rounded to 3 decimals). Row order "
        "does not matter.",
        ("orders", "products"),
        _d25,
    ),
    _t(
        "d26",
        f"{_DEFS}. Using the orders and customers tables, every paid order earns loyalty "
        "points equal to floor(revenue / 10) as an integer, doubled when the customer's tier "
        'is "gold". A loyal customer has at least 400 points in total. For each customer '
        'region (a missing region is labelled "unknown"), report the number of loyal '
        "customers and their combined points. Regions without loyal customers do not appear. "
        "Return exactly three columns: region_label, n_loyal_customers, total_points. Row "
        "order does not matter.",
        ("orders", "customers"),
        _d26,
    ),
    _t(
        "d27",
        f"{_DEFS}. Using the orders table, for customers with at least one order whose status "
        'is "pending", compare the total revenue of their pending orders with their total '
        "paid revenue (0 if they have no paid orders), and report the customers whose pending "
        "total is strictly greater. Also report the age in days, as of 2024-12-31, of their "
        "oldest pending order (2024-12-31 minus the earliest pending order_date). Return "
        "exactly four columns: customer_id, pending_value, paid_revenue (both rounded to 2 "
        "decimals), oldest_pending_days. Row order does not matter.",
        ("orders",),
        _d27,
    ),
    _t(
        "d28",
        f"{_DEFS}. Using the orders and products tables, for each product category compute "
        "total paid revenue in 2023 and in 2024 (by order date) and label the category's "
        'trend: "growing" if the 2024 revenue exceeds 1.1 times the 2023 revenue, '
        '"shrinking" if it is below 0.9 times the 2023 revenue, otherwise "flat" (compare '
        "unrounded totals). Return exactly four columns: category, revenue_2023, "
        "revenue_2024 (both rounded to 2 decimals), trend. Row order does not matter.",
        ("orders", "products"),
        _d28,
    ),
    _t(
        "d29",
        f"{_DEFS}. Using the customers and orders tables, build the distribution of customers "
        "by how many paid orders they placed: for each possible count (including 0 for "
        "customers with no paid orders), how many customers have exactly that many paid "
        "orders. Only counts that occur appear. Return exactly two columns: paid_orders, "
        "n_customers. Sort rows by paid_orders ascending.",
        ("customers", "orders"),
        _d29,
        ordered=True,
    ),
    _t(
        "d30",
        f"{_DEFS}; {_NET_DEF}. Using the customers and orders tables, segment customers by "
        "(region, tier), excluding customers with a missing region. For each segment compute "
        "the number of customers (including those with no paid orders), the segment's total "
        "paid net revenue (customers without paid orders contribute 0), and the average paid "
        "net revenue per customer in the segment. Return the 5 segments with the highest "
        "average, with exactly five columns: region, tier, n_customers, segment_net_revenue "
        "(unrounded), avg_net_per_customer (rounded to 2 decimals). Sort rows by "
        "avg_net_per_customer descending, breaking ties by n_customers descending.",
        ("customers", "orders"),
        _d30,
        ordered=True,
    ),
)
