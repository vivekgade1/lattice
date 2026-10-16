#!/bin/sh
exec python -m lattice.spec check --app harness:make_app --db harness:build_db
