"""A text format given by a regular expression (``regex:<pattern>``; .NET ``[RegularExpression]``,
node F1): a request value the pattern accepts, within the field's length bounds, else none.
The named formats (slug, url, ip, email) are unchanged."""

from __future__ import annotations

import re

import pytest

from lattice.importer import formats


@pytest.mark.parametrize(
    ("pattern", "low", "high"),
    [
        (r"^[0-9A-Za-z]+$", 6, 34),  # H1's accountNumber
        (r"^[A-Z]{2}[0-9]{2}$", None, None),
        (r"^\d{3}-\d{4}$", None, None),
        (r"^(ab|cd)+$", 5, 12),
        (r"^[a-z]+(\.[a-z]+)*$", None, 20),
        (r"[a-f]{3}", None, None),
        (r"^[^@\s]+@[^@\s]+$", 8, None),
    ],
)
def test_the_value_matches_the_pattern_and_the_length_bounds(pattern, low, high):
    value = formats.regex_example(pattern, low, high)
    assert value is not None and re.search(pattern, value)
    assert low is None or len(value) >= low
    assert high is None or len(value) <= high


@pytest.mark.parametrize("pattern", [r"^(?=a)a$", r"^(a)\1$", r"^a{40}$", "(", r"^[a-z]+$"])
def test_a_pattern_it_cannot_satisfy_has_no_value(pattern):
    # lookaround and back references are not generated; 40 characters break the bound; a bad
    # pattern is no pattern; a lower bound above the upper one is none
    low, high = (30, 20) if pattern == r"^[a-z]+$" else (None, 10)
    assert formats.regex_example(pattern, low, high) is None


def test_example_reads_the_regex_format_and_leaves_the_named_ones_alone():
    value = formats.example("regex:^[0-9A-Za-z]+$", 34, 6)
    assert value is not None and re.fullmatch(r"[0-9A-Za-z]{6,34}", value)
    assert formats.example("regex:^(?=a)a$", None, None) is None
    assert formats.example("slug", 4) == "vali"
    assert formats.example("email") == "probe@example.com"
    assert formats.example(None) is None
