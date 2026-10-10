#!/usr/bin/env bash
# Lattice Stop hook: run ./check.sh when it exists and the working tree has edits.
# Default: non-blocking (summary on stderr, exit 0).
# LATTICE_GATE=strict: exit 2 on failure so Claude keeps working until the gate passes.
set -u
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
input="$(cat 2>/dev/null || true)"
# Avoid loops: if a Stop hook already forced a continuation, do not block again.
case "$input" in *'"stop_hook_active":true'*|*'"stop_hook_active": true'*) exit 0 ;; esac
[ -x ./check.sh ] || exit 0
# "Session edited files": any uncommitted change (or not a git repo -> run anyway).
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  [ -n "$(git status --porcelain 2>/dev/null)" ] || exit 0
fi
out="$(./check.sh 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then
  echo "lattice: ./check.sh passed" >&2
  exit 0
fi
{
  echo "lattice: ./check.sh FAILED (exit $rc). Last output:"
  printf '%s\n' "$out" | tail -n 25
} >&2
if [ "${LATTICE_GATE:-}" = "strict" ]; then exit 2; fi
exit 0
