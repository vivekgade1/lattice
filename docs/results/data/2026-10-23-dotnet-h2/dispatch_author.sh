#!/bin/bash
# Dispatch A3, the author of the blind held-out app H2 (docs/results/2026-10-23-dotnet-h2-prereg.md, section 9.1-9.2, 9.4-9.5).
# Run by a LATER node, from the repository root, at the freeze commit. The node that wrote this file never runs it.
#
#   bash docs/results/data/2026-10-23-dotnet-h2/dispatch_author.sh            # the one dispatch
#   bash docs/results/data/2026-10-23-dotnet-h2/dispatch_author.sh fix FILE   # the one fix round: FILE holds the contract-check failures
#
# The claude CLI only, model sonnet, NO API key (env -u ANTHROPIC_API_KEY; every ANTHROPIC_* variable is removed). A3's cap is USD 3.00 in
# total (dispatch + fix round); A3 + W3 together <= USD 4.50. Exit codes: 0 done (next: the contract check), 3 refused (nothing called),
# 4 STOP (a cap, a turn limit, an error or no result: the owner decides), 5 VOID (the transcript audit found a forbidden or outside access).
set -uo pipefail

DATA2="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DATA2/../../../.." && pwd)"
AUTHOR_DIR="$HOME/Documents/dn-h2-author"
OUTD="$DATA2/author"
SPEND="$DATA2/spend.jsonl"
CAP_A3="3.00"
CAP_TOTAL="4.50"
MAX_TURNS=150
FIX_TURNS=60
MODEL="sonnet"
TOOLS="Bash,Read,Write,Edit,Glob,Grep"
PROMPT='Read BRIEF.md in this directory and do everything it asks. Work only inside this directory.'
PY=python3

refuse() { echo "REFUSED: $*" >&2; exit 3; }

spent() {  # spent [who]: USD spent so far by the CLI calls of spend.jsonl (all, or one dispatch's)
  "$PY" - "$SPEND" "${1:-}" <<'E'
import json, sys
from pathlib import Path
f, who = Path(sys.argv[1]), sys.argv[2]
tot = 0.0
if f.is_file():
    for ln in f.read_text().splitlines():
        if ln.strip():
            d = json.loads(ln)
            if not who or d.get("who") == who:
                tot += float(d.get("cost_usd") or 0.0)
print(f"{tot:.4f}")
E
}

has_line() {  # has_line WHO PHASE: is there already such a dispatch in spend.jsonl?
  "$PY" - "$SPEND" "$1" "$2" <<'E'
import json, sys
from pathlib import Path
f, who, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.is_file() else []
sys.exit(0 if any(r.get("who") == who and r.get("phase") == phase for r in rows) else 1)
E
}

record() {  # record WHO PHASE SID CAP RC RESULT_FILE: one spend line from the CLI's own JSON result; prints the stop reason, if any
  "$PY" - "$SPEND" "$1" "$2" "$3" "$4" "$5" "$6" <<'E'
import json, sys, time
from pathlib import Path
spend, who, phase, sid, cap, rc, res = sys.argv[1:8]
doc = None
try:
    doc = json.loads(Path(res).read_text())
except Exception:
    pass
cost = float((doc or {}).get("total_cost_usd") or 0.0)
line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "who": who, "phase": phase, "session_id": sid, "model": "sonnet", "cap_usd": float(cap),
        "cost_usd": cost, "subtype": (doc or {}).get("subtype"), "is_error": (doc or {}).get("is_error"), "num_turns": (doc or {}).get("num_turns"),
        "rc": int(rc), "result_file": res}
with open(spend, "a") as f:
    f.write(json.dumps(line, sort_keys=True) + "\n")
why = ""
if doc is None:
    why = "no JSON result from the CLI"
elif doc.get("subtype") != "success" or doc.get("is_error"):
    why = f"the CLI ended with subtype={doc.get('subtype')} is_error={doc.get('is_error')}"
elif cost >= float(cap) - 0.005:
    why = f"the cost {cost:.4f} reached the cap {cap}"
elif int(rc) != 0:
    why = f"the CLI exited {rc}"
print(why)
E
}

unset_args() {  # every variable that could route around the CLI's login or point at the repository
  UNSET=()
  while IFS='=' read -r name _; do
    case "$name" in
      ANTHROPIC_*|CLAUDE_CODE_USE_*|LATTICE_*|CLAUDECODE|CLAUDE_CODE_ENTRYPOINT|CLAUDE_CODE_SSE_PORT|AI_AGENT|ENABLE_IDE_INTEGRATION|PYTHONPATH|VIRTUAL_ENV|DN_REAL_PATH|AWS_BEARER_TOKEN_BEDROCK)
        UNSET+=(-u "$name") ;;
    esac
  done < <(env)
}

transcript_of() {  # the session's transcript, wherever the CLI keeps its projects
  local sid="$1" base
  for base in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects" "$HOME/.claude/projects"; do
    local hit
    hit="$(ls -t "$base"/*/"$sid".jsonl 2>/dev/null | head -n 1)"
    if [ -n "$hit" ]; then echo "$hit"; return 0; fi
  done
  return 1
}

audit() {  # audit TAG: copy the transcript and audit it; returns 1 when void
  local tag="$1" t arc
  t="$(transcript_of "$SID")" || { echo "no transcript found for session $SID" >&2; return 1; }
  cp -p "$t" "$OUTD/a3-transcript.jsonl"
  "$PY" "$DATA2/audit_transcript.py" "$OUTD/a3-transcript.jsonl" --root "$AUTHOR_DIR" --out "$OUTD/a3-audit-$tag.json"
  arc=$?
  [ "$arc" -eq 0 ]
}

# ---- preconditions (nothing is called if one fails) --------------------------------------------------------------------------------------
[ "$(pwd -P)" = "$REPO" ] || refuse "run from the repository root $REPO"
command -v claude >/dev/null || refuse "no claude CLI on PATH"
command -v "$PY" >/dev/null || refuse "no python3 on PATH"
(cd "$REPO" && shasum -a 256 -c --quiet "$DATA2/freeze.sha256") || refuse "the freeze file does not verify: a frozen file changed"
mkdir -p "$OUTD"
SPENT_ALL="$(spent)"
SPENT_A3="$(spent A3)"
unset_args

if [ "${1:-}" = "fix" ]; then
  FIXFILE="${2:-}"
  [ -n "$FIXFILE" ] && [ -f "$FIXFILE" ] || refuse "usage: $0 fix FILE (FILE holds the contract-check failures)"
  [ -f "$OUTD/a3-session.txt" ] || refuse "no A3 session to resume"
  has_line A3 fix && refuse "the one fix round was already used"
  SID="$(cat "$OUTD/a3-session.txt")"
  CAP="$("$PY" -c "import sys; print(f'{min(float(sys.argv[1]) - float(sys.argv[2]), float(sys.argv[3]) - float(sys.argv[4])):.2f}')" "$CAP_A3" "$SPENT_A3" "$CAP_TOTAL" "$SPENT_ALL")"
  "$PY" -c "import sys; sys.exit(0 if float(sys.argv[1]) >= 0.05 else 1)" "$CAP" || refuse "A3's remaining cap is USD $CAP (< 0.05)"
  FIXPROMPT="The contract check of your service failed on the points below. Fix exactly these, change nothing else, re-run the checks of section 12 of HARNESS_API_DOTNET.md until ALL CHECKS PASSED, and refresh FILES.txt and freeze.sha256.

$(cat "$FIXFILE")"
  cp -p "$FIXFILE" "$OUTD/a3-fix-request.txt"
  rc=0
  (cd "$AUTHOR_DIR" && env -u ANTHROPIC_API_KEY ${UNSET[@]+"${UNSET[@]}"} claude -p "$FIXPROMPT" --resume "$SID" --model "$MODEL" \
      --max-budget-usd "$CAP" --max-turns "$FIX_TURNS" --output-format json --setting-sources "" --strict-mcp-config \
      --disable-slash-commands --tools "$TOOLS" --permission-mode dontAsk --allowedTools "$TOOLS") \
      > "$OUTD/a3-fix-result.json" 2> "$OUTD/a3-fix-stderr.txt" || rc=$?
  WHY="$(record A3 fix "$SID" "$CAP" "$rc" "$OUTD/a3-fix-result.json")"
  audit fix; ARC=$?
  echo "A3 fix round: session $SID, cap USD $CAP, spent so far (all) USD $(spent)"
  if [ -n "$WHY" ]; then echo "STOP: $WHY. The fix round is not used; the owner decides." >&2; exit 4; fi
  if [ "$ARC" -ne 0 ]; then echo "VOID: the transcript audit failed (see $OUTD/a3-audit-fix.json); the owner decides." >&2; exit 5; fi
  echo "next: the contract check again (once)"; exit 0
fi

# ---- the one dispatch ----------------------------------------------------------------------------------------------------------------------
has_line A3 dispatch && refuse "A3 was already dispatched (see $SPEND)"
"$PY" -c "import sys; sys.exit(0 if float(sys.argv[1]) + float(sys.argv[2]) <= float(sys.argv[3]) + 1e-9 else 1)" "$SPENT_ALL" "$CAP_A3" "$CAP_TOTAL" \
  || refuse "USD $SPENT_ALL already spent: A3's cap of $CAP_A3 would exceed the total $CAP_TOTAL"
if [ -e "$AUTHOR_DIR" ] && [ -n "$(ls -A "$AUTHOR_DIR" 2>/dev/null)" ]; then refuse "$AUTHOR_DIR exists and is not empty"; fi
mkdir -p "$AUTHOR_DIR"
case "$(cd "$AUTHOR_DIR" && pwd -P)" in "$REPO"*) refuse "the author directory lies inside the repository" ;; esac
for f in BRIEF.md HARNESS_API_DOTNET.md TRUTH_SCHEMA_DOTNET.md; do cp "$DATA2/brief/$f" "$AUTHOR_DIR/$f"; done
(cd "$AUTHOR_DIR" && shasum -a 256 BRIEF.md HARNESS_API_DOTNET.md TRUTH_SCHEMA_DOTNET.md) > "$OUTD/a3-inputs.sha256"
SID="$(uuidgen | tr 'A-Z' 'a-z')"
echo "$SID" > "$OUTD/a3-session.txt"
claude --version > "$OUTD/a3-claude-version.txt" 2>&1 || true
rc=0
(cd "$AUTHOR_DIR" && env -u ANTHROPIC_API_KEY ${UNSET[@]+"${UNSET[@]}"} claude -p "$PROMPT" --model "$MODEL" --max-budget-usd "$CAP_A3" \
    --max-turns "$MAX_TURNS" --output-format json --session-id "$SID" --setting-sources "" --strict-mcp-config \
    --disable-slash-commands --tools "$TOOLS" --permission-mode dontAsk --allowedTools "$TOOLS") \
    > "$OUTD/a3-result.json" 2> "$OUTD/a3-stderr.txt" || rc=$?
WHY="$(record A3 dispatch "$SID" "$CAP_A3" "$rc" "$OUTD/a3-result.json")"
audit dispatch; ARC=$?
echo "A3: session $SID, cap USD $CAP_A3, spent USD $(spent A3) (all: $(spent))"
if [ -n "$WHY" ]; then echo "STOP: $WHY. The deliverable is not contract-checked or used; nothing is re-dispatched; the owner decides." >&2; exit 4; fi
if [ "$ARC" -ne 0 ]; then echo "VOID: the transcript audit found a forbidden or outside access (see $OUTD/a3-audit-dispatch.json): confirm by reading the call; the owner decides." >&2; exit 5; fi
echo "next: the contract check (prereg 3.2); write $OUTD/contract-check.json"
exit 0
