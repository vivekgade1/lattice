#!/bin/sh
if [ -n "$FAST" ]; then
  python -m lattice.spec check --app harness:make_app --db harness:build_db
else
  python -m lattice.spec check --app harness:make_app --db harness:build_seed_db
fi
