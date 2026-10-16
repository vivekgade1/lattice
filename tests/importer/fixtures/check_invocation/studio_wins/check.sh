#!/bin/sh
exec python -m lattice.spec check --app harness:make_app --db harness:build_seed_db \
  --login harness:login --services harness:SERVICES --database sqlite
