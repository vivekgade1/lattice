#!/bin/sh
set -e
N=${1:-5}; shift 2>/dev/null || true
cd "$(dirname "$0")"
exec uv run python -m lattice.spec check \
  --spec "spec.tickets:t$N" \
  --app harness:make_app --db harness:build_seed_db --runtime harness:RUNTIME \
  --login seedmod:login --layers ABCDX --go-src . --x-repeats 5 "$@"
