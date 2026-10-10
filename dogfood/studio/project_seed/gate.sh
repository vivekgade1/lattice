#!/bin/sh
# TEST ONLY: the dogfood rebuild gate. Two checks, one per area of the seed's spec:
# writing.txt stands for scenario:create_a_note (Writing notes), reading.txt for
# scenario:read_own_and_other (Reading notes). The stub agent writes them (stub-rebuild.json);
# Studio runs the gate in the rebuild's worktree.
n=0
[ -f writing.txt ] || n=$((n + 1))
[ -f reading.txt ] || n=$((n + 1))
if [ "$n" = 0 ]; then
  echo "PASS: 0 findings (G 0)"
  exit 0
fi
echo "FAIL: $n findings (G $n)"
[ -f writing.txt ] || echo "FAIL writing.txt is missing (scenario:create_a_note)"
[ -f reading.txt ] || echo "FAIL reading.txt is missing (scenario:read_own_and_other)"
exit 1
