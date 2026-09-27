"""The loop corpus (sub-project 1b, spec 2026-09-27 lattice-loops §6 item 5): real-style
Python loops, each with its Lattice equivalent; the seed test set of the importer
(sub-project 2). See README.md; the equivalence tests are tests/loops/test_loop_corpus.py."""

from __future__ import annotations

TABULAR = (
    "l01_running_balance",
    "l02_previous_delta",
    "l03_rank_within_group",
    "l04_moving_average",
    "l05_category_descendants",
    "l06_ancestors_path",
    "l08_prune_orphans",
    "l09_converge_forecast",
    "l11_ties_order_key",
)
API = ("l07_org_chart", "l10_notify_watchers")
NATIVE_ISLANDS = ("l12_login_state_machine",)
ITEMS = tuple(sorted(TABULAR + API + NATIVE_ISLANDS))
