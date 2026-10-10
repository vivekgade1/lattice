#!/bin/sh
# TEST ONLY: the dogfood rebuild gate. Two checks, one per area of the seed's spec:
# writing.txt stands for scenario:create_a_note (Writing notes), reading.txt for
# scenario:read_own_and_other (Reading notes). The stub agent writes them (stub-rebuild.json);
# Studio runs the gate in the rebuild's worktree. A third check passes unless left.txt and
# right.txt both exist (scenario:no_token): two plan nodes that each pass alone and clash
# once merged (API.md v1.9, the full gate after a level).
n=0
[ -f writing.txt ] || n=$((n + 1))
[ -f reading.txt ] || n=$((n + 1))
if [ -f left.txt ] && [ -f right.txt ]; then n=$((n + 1)); fi
if [ "$n" = 0 ]; then
  echo "PASS: 0 findings (G 0)"
  exit 0
fi
echo "FAIL: $n findings (G $n)"
[ -f writing.txt ] || echo "FAIL writing.txt is missing (scenario:create_a_note)"
[ -f reading.txt ] || echo "FAIL reading.txt is missing (scenario:read_own_and_other)"
if [ -f left.txt ] && [ -f right.txt ]; then
  echo "FAIL left.txt and right.txt clash (scenario:no_token)"
fi
exit 1
