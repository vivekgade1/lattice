#!/bin/zsh
# usage: runcheck.sh vN LAYERS TAG
S=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/w-j1m
source $S/env.sh $1
cd $S/streak
mkdir -p $S/m3
t0=$(date +%s)
$PV -m lattice.spec check --spec spec:SPEC \
  --app harness:make_app --db harness:build_db --runtime harness:RUNTIME \
  --login harness:login --services harness:SERVICES \
  --database mysql --layers $2 --go-src $S/streak --x-repeats 5 --json \
  > $S/m3/$3.json 2> $S/m3/$3.err
echo "rc=$? wall=$(( $(date +%s) - t0 ))s" > $S/m3/$3.rc
