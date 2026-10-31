#!/usr/bin/env bash
# Lattice Stop hook: the completion gate. Runs ./check.sh when the session may have changed
# the project, and blocks the stop (exit 2) while it fails.
#
# LATTICE_GATE            strict (default): block on a failing check, at most LATTICE_GATE_MAX_BLOCKS
#                         times in a row (see below). warn: print the summary, never block.
#                         off: do nothing.
# LATTICE_GATE_MAX_BLOCKS consecutive blocks per stop before the hook lets the session end with a
#                         "gate still failing" message (default 3). The loop is bounded by this
#                         counter, not by Claude Code: a strict gate forces at most N extra rounds
#                         per stop, not until green. Always pass --max-turns as well.
#
# When it runs: the working tree is dirty, or HEAD differs from the HEAD recorded at the last
# passing check (so work that was committed is still checked). In a Lattice project (spec.py and
# spec.trace.json) a missing or non-executable ./check.sh is a block, not a silent pass; any other
# project without ./check.sh is a no-op.
#
# State (the block counter per session, the last passing HEAD) lives in <git dir>/lattice-gate,
# or under $TMPDIR when there is no git dir. stop_hook_active (the hook input's "we are already in
# a forced continuation") only tells a fresh stop, which resets the counter, from a continuation,
# which keeps counting; it is never a free pass.
# Portable bash (3.2): no associative arrays.
set -u
mode="${LATTICE_GATE:-strict}"
[ "$mode" = "off" ] && exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
input="$(cat 2>/dev/null || true)"

max="${LATTICE_GATE_MAX_BLOCKS:-3}"
case "$max" in ''|*[!0-9]*) max=3 ;; esac

active=0
case "$input" in *'"stop_hook_active":true'*|*'"stop_hook_active": true'*) active=1 ;; esac
sid="$(printf '%s' "$input" | sed -n 's/.*"session_id" *: *"\([^"]*\)".*/\1/p' | head -n 1 | tr -c 'A-Za-z0-9_-' '_')"
[ -n "$sid" ] || sid="nosession"

in_git=0
head_rev="none"; tree_rev="none"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  in_git=1
  head_rev="$(git rev-parse -q --verify HEAD 2>/dev/null || echo none)"
  tree_rev="$(git rev-parse -q --verify 'HEAD^{tree}' 2>/dev/null || echo none)"
  state="$(git rev-parse --absolute-git-dir 2>/dev/null)/lattice-gate"
else
  state="${TMPDIR:-/tmp}/lattice-gate-$(pwd | cksum | tr -d ' ')"
fi
mkdir -p "$state" 2>/dev/null || state="${TMPDIR:-/tmp}/lattice-gate-$(pwd | cksum | tr -d ' ')"
mkdir -p "$state" 2>/dev/null || true
count_file="$state/blocks-$sid"
pass_file="$state/last-pass"

lattice_project=0
[ -f spec.py ] && [ -f spec.trace.json ] && lattice_project=1

read_count() {
  local n=0
  [ -f "$count_file" ] && n="$(cat "$count_file" 2>/dev/null || echo 0)"
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  printf '%s' "$n"
}

# A fresh stop (not inside a forced continuation) starts a new round count.
[ "$active" -eq 0 ] && rm -f "$count_file" 2>/dev/null

# block <message...>: strict -> exit 2 up to the cap, then exit 0 saying the gate is still failing.
block() {
  { printf '%s\n' "$@"; } >&2
  [ "$mode" = "warn" ] && exit 0
  local n
  n="$(read_count)"
  if [ "$n" -ge "$max" ]; then
    echo "lattice: the gate is still failing after $n blocked stops (LATTICE_GATE_MAX_BLOCKS=$max); letting the session stop. It is NOT green." >&2
    exit 0
  fi
  echo $((n + 1)) > "$count_file" 2>/dev/null
  exit 2
}

if [ ! -x ./check.sh ]; then
  if [ "$lattice_project" -eq 1 ]; then
    block "lattice: ./check.sh is missing or not executable in a Lattice project. The gate cannot run; do not delete or replace it. Restore it (git checkout -- check.sh) or tell the owner."
  fi
  exit 0
fi

if [ "$in_git" -eq 1 ]; then
  last=""
  [ -f "$pass_file" ] && last="$(sed -n 's/^head=//p' "$pass_file" 2>/dev/null | head -n 1)"
  if [ -z "$(git status --porcelain 2>/dev/null)" ] && [ "$last" = "$head_rev" ]; then
    exit 0  # clean, and HEAD is the one that last passed
  fi
fi

out="$(./check.sh 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then
  echo "lattice: ./check.sh passed" >&2
  rm -f "$count_file" 2>/dev/null
  if [ "$in_git" -eq 1 ]; then
    printf 'head=%s\ntree=%s\n' "$head_rev" "$tree_rev" > "$pass_file" 2>/dev/null
  fi
  exit 0
fi
rm -f "$pass_file" 2>/dev/null
block "lattice: ./check.sh FAILED (exit $rc). Last output:" "$(printf '%s\n' "$out" | tail -n 25)"
