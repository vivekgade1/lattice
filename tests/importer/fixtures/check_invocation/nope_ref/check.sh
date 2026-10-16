#!/bin/sh
exec python -m lattice.spec check --app harness:nope --db harness:build_db
