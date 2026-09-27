"""Edited copies of the vendored template, loaded in-process beside it.

A *variant* is the template package ``bench.template_app.app`` with some of its files
replaced: an overlay ``{relative path: source}`` (paths relative to
``bench/template_app/app``, e.g. ``"api/routes/items.py"``; new files are allowed). The
reference implementations of the edit-slice tickets are variants, and so is any
Python-edited candidate app (arm P of the slice), so both are built the same way.

:func:`load_variant` imports the variant as a fresh package (``_edit_slice_variant_<hash>``)
through an import hook, with two redirections applied to every module of the variant:

* imports of ``bench.template_app.app[.x]`` resolve to the variant's own modules, so the
  sources stay exactly as written against the template (no rewriting);
* ``sqlmodel.SQLModel`` resolves to a subclass bound to a fresh SQLAlchemy ``registry``,
  so the variant's table models (``item``, ``user`` -- possibly with new columns) live
  in their own metadata and do not clash with the template's or another variant's.

Files not in the overlay are read from the template. Each module is compiled with a real
file name (the overlay's file on disk when known, the template's otherwise, or a
``linecache`` entry for in-memory sources), so ``inspect.getsource`` works on variant
functions (the verifier's branch coverage needs it). Variants are cached by content.
"""

from __future__ import annotations

import builtins
import hashlib
import importlib
import importlib.abc
import importlib.util
import linecache
import sys
import types
from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Any

import sqlmodel
from fastapi import FastAPI
from sqlalchemy.orm import registry
from sqlmodel import Session

from bench.template_app import sqlite_engine

TEMPLATE_PACKAGE = "bench.template_app.app"
TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "template_app" / "app"


def template_source(rel: str) -> str:
    """The template's own text of ``rel`` (relative to ``bench/template_app/app``)."""
    return (TEMPLATE_ROOT / rel).read_text()


class Variant:
    """A loaded variant: its package name, overlay, model base and :meth:`make_app`."""

    def __init__(self, package: str, files: Mapping[str, str], origins: Mapping[str, Path]):
        self.package = package
        self.files = dict(files)
        self.origins = dict(origins)

        class SQLModel(sqlmodel.SQLModel, registry=registry()):
            pass

        SQLModel.__module__ = package
        self.base: type[sqlmodel.SQLModel] = SQLModel
        proxy = types.ModuleType("sqlmodel")
        proxy.__dict__.update(sqlmodel.__dict__)
        proxy.SQLModel = SQLModel  # type: ignore[attr-defined]
        self._sqlmodel = proxy
        self.builtins = dict(builtins.__dict__)
        self.builtins["__import__"] = self._import

    # -- the import redirections
    def _import(self, name: str, globals: Any = None, locals: Any = None,
                fromlist: Any = (), level: int = 0) -> Any:  # fmt: skip
        if level == 0:
            if name == "sqlmodel":
                return self._sqlmodel
            if name == TEMPLATE_PACKAGE or name.startswith(TEMPLATE_PACKAGE + "."):
                name = self.package + name[len(TEMPLATE_PACKAGE) :]
                if not fromlist:  # `import bench.template_app.app.x` binds `bench`: unsupported
                    raise ImportError(f"use `from ... import` for {name!r} in a variant")
        return builtins.__import__(name, globals, locals, fromlist, level)

    # -- sources
    def rel_path(self, fullname: str) -> tuple[str, bool] | None:
        """(relative file, is package) of a module of this variant, or None."""
        if fullname == self.package:
            return "__init__.py", True
        rel = fullname[len(self.package) + 1 :].replace(".", "/")
        if (TEMPLATE_ROOT / rel).is_dir() or any(k.startswith(rel + "/") for k in self.files):
            return rel + "/__init__.py", True
        if rel + ".py" in self.files or (TEMPLATE_ROOT / (rel + ".py")).is_file():
            return rel + ".py", False
        return None

    def source(self, rel: str) -> tuple[str, str]:
        """(source text, file name to compile under) of ``rel``."""
        if rel in self.files:
            text = self.files[rel]
            origin = self.origins.get(rel)
            if origin is not None and origin.is_file() and origin.read_text() == text:
                return text, str(origin)
            filename = f"<variant {self.package}>/{rel}"
            lines = text.splitlines(keepends=True)
            linecache.cache[filename] = (len(text), None, lines, filename)
            return text, filename
        path = TEMPLATE_ROOT / rel
        if not path.is_file() and rel.endswith("__init__.py"):
            return "", f"<variant {self.package}>/{rel}"
        return path.read_text(), str(path)

    # -- modules and the app
    def module(self, dotted: str) -> types.ModuleType:
        """The variant's module ``dotted`` (e.g. ``"api.routes.items"``)."""
        return importlib.import_module(f"{self.package}.{dotted}" if dotted else self.package)

    @property
    def metadata(self) -> Any:
        return self.base.metadata

    def make_app(self, db_path: str | Path) -> FastAPI:
        """The variant app on the SQLite file at ``db_path``, as ``make_app`` builds the
        template: missing tables created from the variant's models, ``deps.get_db``
        routed to that file, the engine on ``app.state.engine``."""
        self.module("models")
        deps = self.module("api.deps")
        main = self.module("main")
        engine = sqlite_engine(db_path)
        self.metadata.create_all(engine)

        def get_db() -> Generator[Session]:
            with Session(engine) as session:
                yield session

        app = main.create_app()
        app.dependency_overrides[deps.get_db] = get_db
        app.state.engine = engine
        return app


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self) -> None:
        self.variants: dict[str, Variant] = {}

    def _variant(self, fullname: str) -> Variant | None:
        return self.variants.get(fullname.split(".", 1)[0])

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        v = self._variant(fullname)
        if v is None:
            return None
        found = v.rel_path(fullname)
        if found is None:
            return None
        return importlib.util.spec_from_loader(fullname, self, is_package=found[1])

    def create_module(self, spec: Any) -> None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        v = self._variant(module.__name__)
        assert v is not None
        found = v.rel_path(module.__name__)
        assert found is not None
        text, filename = v.source(found[0])
        module.__file__ = filename
        module.__dict__["__builtins__"] = v.builtins
        exec(compile(text, filename, "exec", dont_inherit=True), module.__dict__)


_FINDER = _Finder()
sys.meta_path.insert(0, _FINDER)


def load_variant(files: Mapping[str, str], *, origins: Mapping[str, Path] | None = None) -> Variant:
    """The template with ``files`` replaced (see the module docstring); cached by content.

    ``origins`` maps overlay paths to the files they were read from (for tracebacks and
    ``inspect``). The variant's modules are imported lazily, by :meth:`Variant.make_app`
    or :meth:`Variant.module`; a source that does not import raises there."""
    key = repr(sorted(files.items()))
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    package = f"_edit_slice_variant_{digest}"
    v = _FINDER.variants.get(package)
    if v is None:
        v = Variant(package, files, origins or {})
        _FINDER.variants[package] = v
    return v
