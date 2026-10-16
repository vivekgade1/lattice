#!/bin/sh
APP=harness:make_app
exec python -m lattice.spec check --app "$APP" --db harness:build_db
