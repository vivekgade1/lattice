#!/bin/sh
# TEST ONLY: the dogfood rebuild gate, shaped like `lattice spec check` for Studio's scoped
# gates (API.md v1.11): it takes --layers L (default ABCDX), --x-repeats N, --await-cap S and
# --only-file F (a JSON array of "scenario:<name>" selectors; a name that is not a scenario
# exits 2, like the real check) and checks only the selected scenarios. Checks, one per
# scenario of the seed's spec:
#   scenario:create_a_note       writing.txt exists; with layer X (races) also: race.txt does
#                                not say "racy" -- a race-only failure, invisible to a gate
#                                run with --layers CD
#   scenario:read_own_and_other  reading.txt exists
#   scenario:no_token            not both left.txt and right.txt -- two plan nodes that each
#                                pass alone and clash once merged (cross-area, API.md v1.9)
#   scenario:text_bounds         always passes
# The stub agent writes the files (stub-rebuild.json); Studio runs the gate in the worktree.
layers=ABCDX
only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --layers) layers="$2"; shift 2 ;;
    --x-repeats|--await-cap) shift 2 ;;
    --only-file) only="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -n "$only" ]; then
  for s in $(grep -o '"scenario:[^"]*"' "$only" | tr -d '"' | sed 's/^scenario://'); do
    case "$s" in
      create_a_note|text_bounds|read_own_and_other|no_token) ;;
      *) echo "error: --only 'scenario:$s' matches no scenario of the spec" >&2; exit 2 ;;
    esac
  done
fi
selected() {
  [ -z "$only" ] && return 0
  grep -q "\"scenario:$1\"" "$only"
}
n=0
out=""
add() {
  n=$((n + 1))
  out="$out$1
"
}
if selected create_a_note; then
  [ -f writing.txt ] || add "FAIL writing.txt is missing (scenario:create_a_note)"
  case "$layers" in
    *X*) if [ -f race.txt ] && grep -q racy race.txt; then
           add "FAIL two concurrent POST /notes lost a note: race.txt is racy (scenario:create_a_note)"
         fi ;;
  esac
fi
if selected read_own_and_other; then
  [ -f reading.txt ] || add "FAIL reading.txt is missing (scenario:read_own_and_other)"
fi
if selected no_token && [ -f left.txt ] && [ -f right.txt ]; then
  add "FAIL left.txt and right.txt clash (scenario:no_token)"
fi
if [ "$n" = 0 ]; then
  echo "PASS: 0 findings (G 0)"
  exit 0
fi
echo "FAIL: $n findings (G $n)"
printf '%s' "$out"
exit 1
