"""Text formats a front end reads from a field's type (``FieldSpec.format``): Django's
``SlugField`` and ``<slug:...>`` path converter, ``URLField``, ``GenericIPAddressField``,
``EmailField`` (and their DRF serializer fields), a validator of one of them on a
``CharField``. A field of a format only takes values of it: the seed's rows and the
probes' requests give it such values, else the app (or its router) refuses them for
the format's sake, and every rule the probe was about goes unseen -- or, read as the
rule's answer, is falsely refuted (H1 drf_blog's ``{slug}`` routes, 2026-10-05).

Imported lazily, only for a field that has a format: the FastAPI front end sets none,
so its import path (and the Hypothesis constant pool its goldens depend on, see
:mod:`lattice.importer.suite`) never loads this module."""

from __future__ import annotations

import re

SLUG = "slug"
URL = "url"
IP = "ip"
EMAIL = "email"
FORMATS = (SLUG, URL, IP, EMAIL)

# a valid value of each format, for a request parameter (ContractParam.example)
EXAMPLES = {
    SLUG: "valid-slug",
    URL: "https://example.com/probe",
    IP: "192.0.2.1",
    EMAIL: "probe@example.com",
}

# what a value of each format matches (Django's validators, simplified)
SHAPES = {
    SLUG: re.compile(r"^[-a-zA-Z0-9_]+$"),
    URL: re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.I),
    IP: re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),
    EMAIL: re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I),
}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or SLUG


def seed_value(fmt: str, stem: str, i: int, max_len: int | None = None) -> str:
    """Row ``i``'s value of a column of the format: ``stem`` (the seed's text, "Slug 01")
    turned into it, distinct per row, at most ``max_len`` characters where the format
    allows."""
    slug = _slugify(stem)
    if fmt == URL:
        value = f"https://example.com/{slug}"
    elif fmt == IP:
        value = f"192.0.2.{i % 254 + 1}"
    elif fmt == EMAIL:
        value = f"{slug}@example.com"
    else:
        value = slug
    if max_len is not None and len(value) > max_len and fmt == SLUG:
        tail = f"{i + 1:02d}"
        value = (value[: max(max_len - len(tail) - 1, 0)] + "-" + tail).strip("-")[:max_len]
    return value


def example(fmt: str | None, max_len: int | None = None) -> str | None:
    """A request value of the format (None: no format); a slug cut to ``max_len``."""
    if fmt not in EXAMPLES:
        return None
    value = EXAMPLES[fmt]
    if max_len is not None and fmt == SLUG:
        value = value[:max_len]
    return value


def of_django_field(field: object) -> str | None:
    """The format of a Django model field: its class (``SlugField``, ``URLField``,
    ``GenericIPAddressField``, ``EmailField``), else one of its validators."""
    from django.core import validators as v

    names = {k.__name__ for k in type(field).__mro__ if k.__module__.startswith("django.db")}
    by_class = (("SlugField", SLUG), ("URLField", URL), ("GenericIPAddressField", IP),
                ("IPAddressField", IP), ("EmailField", EMAIL))  # fmt: skip
    for name, fmt in by_class:
        if name in names:
            return fmt
    return _of_validators(getattr(field, "validators", ()), v)


def of_drf_field(field: object) -> str | None:
    """The format of a DRF serializer field: its class, else one of its validators."""
    from django.core import validators as v
    from rest_framework import fields as F

    by_class = ((F.SlugField, SLUG), (F.URLField, URL), (F.IPAddressField, IP),
                (F.EmailField, EMAIL))  # fmt: skip
    for klass, fmt in by_class:
        if isinstance(field, klass):
            return fmt
    return _of_validators(getattr(field, "validators", ()), v)


def _of_validators(validators: object, v: object) -> str | None:
    for val in validators or ():  # type: ignore[attr-defined]
        if val in (v.validate_slug, v.validate_unicode_slug):  # type: ignore[attr-defined]
            return SLUG
        if isinstance(val, v.URLValidator):  # type: ignore[attr-defined]
            return URL
        if isinstance(val, v.EmailValidator):  # type: ignore[attr-defined]
            return EMAIL
        if val in (v.validate_ipv4_address, v.validate_ipv46_address):  # type: ignore[attr-defined]
            return IP
    return None


def of_converter(converter: object) -> str | None:
    """The format a Django path converter takes (``<slug:...>``)."""
    return SLUG if type(converter).__name__ == "SlugConverter" else None


__all__ = ["EXAMPLES", "FORMATS", "SHAPES", "example", "of_converter", "of_django_field",
           "of_drf_field", "seed_value"]  # fmt: skip
