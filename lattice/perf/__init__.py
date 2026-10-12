"""Performance budgets for an app built from its spec (spike 1a): statements per request
(and latency) of chosen endpoints, measured on a throwaway database and compared with a
side file ``perf.budget.json``. Not a build gate and not part of ``lattice check``; run it
with ``python -m lattice.perf`` or ``lattice perf``."""

from lattice.perf.budget import BUDGET_FILE, Budget, BudgetError, Report, fit, judge, load, parse

__all__ = ["BUDGET_FILE", "Budget", "BudgetError", "Report", "fit", "judge", "load", "parse"]
