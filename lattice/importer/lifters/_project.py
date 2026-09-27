"""Returning a row: the response model's columns, the table's private columns never."""

from __future__ import annotations

from lattice.importer.extract import TableModel
from lattice.importer.lifters.base import RouteSource, Step, str_list


def response_fields(
    route: RouteSource, name: str | None, table: TableModel
) -> tuple[str, ...] | None:
    """The fields of response model ``name`` when each is a column of ``table``."""
    model = route.data_model(name)
    if model is None:
        return None
    columns = {c.name for c in table.columns}
    fields = tuple(f.name for f in model.fields)
    return fields if fields and set(fields) <= columns else None


def project(
    route: RouteSource,
    table: TableModel,
    source: str,
    name: str = "shown",
    response: str | None = None,
) -> tuple[tuple[Step, ...], str] | None:
    """Steps returning the row ``source`` (a ``{@var}``/``{#name}`` hole) as the
    response model: a ``select`` of its fields when the table has private columns
    (which never appear in a response), else the row as it is (the response model picks
    its fields). (steps, the returned hole)."""
    fields = response_fields(route, response or route.route.response_model, table)
    if fields is None:
        return None
    private = set(route.private(table))
    if private & set(fields):
        return None
    if not private:
        return (), source
    return (Step(name, "select", (source,), (("cols", str_list(fields)),)),), "{#" + name + "}"
