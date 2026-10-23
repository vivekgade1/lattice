#!/bin/sh
# The gate: build the service, check it against spec.py (layer D). Exit 0 = clean.
# LATTICE_REBUILD set (Studio running this as a gate) also writes metrics/last-check.json.
set -e
cd "$(dirname "$0")"
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
dotnet build dnbuild.csproj -c Release -o bin/out --nologo -v q
set -- --spec spec:SPEC --app harness:make_app --db harness:build_db --runtime harness:RUNTIME --layers D "$@"
if [ -n "${LATTICE_REBUILD:-}" ] || [ "${DN_CHECK_JSON:-0}" = "1" ]; then
  mkdir -p metrics
  set +e
  uv run python -m lattice.spec check "$@" --json > metrics/last-check.json
  rc=$?
  set -e
  uv run python -c "import json; d=json.load(open('metrics/last-check.json')); print(json.dumps({k: d[k] for k in d if k not in ('cases','findings')}, indent=1)[:3000])" || true
  exit $rc
fi
exec uv run python -m lattice.spec check "$@"
