"""list of every row (the admin list): the list-with-count block with no filter, then
the envelope -> ``fetch`` / ``select`` (a table with private columns) / ``sort`` /
``page(..., total=True)``. Whether every row may be listed to the route's callers is the
checker's to decide (an owned table on a Customer route is refused), not the lifter's."""

from __future__ import annotations

from lattice.importer.extract import RepoModel
from lattice.importer.lifters._listing import listing, page_fragment, tail
from lattice.importer.lifters.base import LiftFragment, RouteSource

IDIOM = "list every row (offset/limit + count) -> page(total=True)"


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    body = list(route.body)
    for n in range(len(body) - 1, 1, -1):
        found = listing(route, body[:n])
        if found is None:
            continue
        if found.owner_filter:
            return None
        item = tail(route, body[n:], found)
        if item is None:
            return None
        return page_fragment(
            route, __name__.rsplit(".", 1)[-1], IDIOM, tuple(range(len(body))), found, item
        )
    return None
