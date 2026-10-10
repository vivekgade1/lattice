#!/bin/sh
# The Lattice gate for Lattice Studio's own server (dogfooding): check the real server
# (python -m lattice.studio, TEST-ONLY stub LLM) against spec.py, as a black box over HTTP.
# Layers D (scenarios) and X (races: Studio has no tables, so nothing to race). Layers A, B,
# C and R have nothing to bind to: see harness.py. Exit 0 = clean, 1 = findings, 2 = the
# check could not run. Usage: ./check.sh [--json]
# LATTICE_STUDIO_PYTHONPATH=<dir> serves the server from a patched copy (the mutation run).
set -e
cd "$(dirname "$0")"
exec uv run python -m lattice.spec check \
  --spec spec:SPEC --app harness:make_app --db harness:build_db --runtime harness:RUNTIME \
  --layers DX "$@"
