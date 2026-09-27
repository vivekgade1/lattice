"""``/me``: ``return <caller>`` -> the caller's row (``user``, or the row an earlier
``update`` of the caller returned), projected to the response model -- the users table
has private columns (the password hash), so this is a ``select`` of the response
model's fields. Read-me is this alone; update-me is ``partial_update`` (and
``unique_check``) on the caller followed by this."""

from __future__ import annotations

import ast

from lattice.importer.extract import RepoModel
from lattice.importer.lifters._project import project
from lattice.importer.lifters.base import LiftFragment, RouteSource, is_name

IDIOM = "return current_user -> select(user, cols=<response model>)"


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    caller, users = route.user, route.users_table
    if caller is None or users is None or not route.body:
        return None
    last = route.body[-1]
    if not (isinstance(last, ast.Return) and is_name(last.value, caller)):
        return None
    found = project(route, users, "{@" + caller + "}")
    if found is None:
        return None
    steps, returned = found
    return LiftFragment(
        lifter=__name__.rsplit(".", 1)[-1],
        idiom=IDIOM,
        stmts=(len(route.body) - 1,),
        steps=steps,
        returns=returned,
    )
