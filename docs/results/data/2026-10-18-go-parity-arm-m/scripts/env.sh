# usage: source env.sh vN   (v0 v1 v2 v3)
S=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/w-j1m
PV=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/perfengines/perfvenv/bin/python
if [ "$1" = v3 ]; then LIB=/Users/vivekgade/Documents/lattice-ma; else LIB=$S/lib/$1; fi
export PYTHONPATH=$LIB:$S/streak PYTHONDONTWRITEBYTECODE=1 UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv
export LATTICE_CONTAINER_OWNER=w-j1m
export PATH=/opt/homebrew/bin:$PATH
unset LATTICE_MYSQL_URL
