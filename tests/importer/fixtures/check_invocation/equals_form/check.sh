#!/bin/sh
python -m lattice.spec check --app=harness:make_app --db=harness:build_db --login=harness:login \
  --services=harness:SERVICES --database=sqlite
