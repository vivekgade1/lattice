#!/bin/sh
# python -m lattice.spec check --app wrong:app --db wrong:db
echo "python -m lattice.spec check --app wrong:app --db wrong:db"
python -c "import sys; print(sys.argv)" ; true
python -m lattice.spec check \
  --app harness:make_app \
  --db harness:build_db   # the real one
