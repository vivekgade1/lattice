#!/bin/sh
# TEST ONLY: the dogfood rebuild gate. It passes iff built.txt exists (the stub agent writes
# it in round 2 of stub-rebuild.json); Studio runs it in the rebuild's worktree.
if [ -f built.txt ]; then
  echo "PASS: 0 findings (G 0)"
  exit 0
fi
echo "FAIL: 1 finding (G 1)"
echo "FAIL built.txt is missing (scenario:built)"
exit 1
