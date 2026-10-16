#!/bin/sh
set -e
cd "$(dirname "$0")"
go build -o bin/server ./cmd/server
set -- --spec spec:SPEC \
  --app harness:make_app --db harness:build_db --runtime harness:RUNTIME \
  --login harness:login --services harness:SERVICES \
  --database mysql --layers ABCDX --go-src . --x-repeats 5 "$@"
if [ "${CHECK_JSON:-0}" = "1" ] || [ -n "${LATTICE_REBUILD:-}" ]; then
  mkdir -p metrics
  set +e
  uv run python -m lattice.spec check "$@" --json > metrics/last-check.json
  rc=$?
  set -e
  uv run python -c "import json,sys; d=json.load(open('metrics/last-check.json')); print(d)" || true
  exit $rc
fi
exec uv run python -m lattice.spec check "$@"
