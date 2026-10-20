"""``candidate_item_ids``: each report item's id in the corrected candidate spec.

A correction renames an item; the ``#n`` duplicates of a route's errors are then renumbered,
as ``probes._error_ids`` numbers them."""

from __future__ import annotations

from lattice.importer.review import candidate_item_ids


def test_a_corrected_duplicate_moves_its_sibling_up() -> None:
    items = {
        "R3 GET /x 404 not_found": {
            "verdict": "verified (corrected)",
            "correction": {"corrected": "R3 GET /x 404"},
        },
        "R3 GET /x 404 not_found #2": {"verdict": "refuted"},
        "R3 GET /x 422": {"verdict": "verified"},
    }
    ids = candidate_item_ids(items)
    assert ids["R3 GET /x 404 not_found"] == "R3 GET /x 404"
    assert ids["R3 GET /x 404 not_found #2"] == "R3 GET /x 404 not_found"
    assert ids["R3 GET /x 422"] == "R3 GET /x 422"


def test_without_corrections_every_id_is_its_own() -> None:
    items = {"R1 GET /x": {}, "R3 GET /x 404": {}, "R3 GET /x 404 #2": {}, "R3 GET /x 404 #3": {}}
    assert candidate_item_ids(items) == {i: i for i in items}
