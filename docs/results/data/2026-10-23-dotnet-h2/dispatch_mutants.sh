#!/bin/bash
# Dispatch W3, the writer of H2's 16 held-out mutants (docs/results/2026-10-23-dotnet-h2-prereg.md, sections 4.1, 9.3-9.5).
# Run by a LATER node, from the repository root, after the contract check passed and H2 is archived (h2.tgz, h2-truth.json).
# The node that wrote this file never runs it.
#
#   bash docs/results/data/2026-10-23-dotnet-h2/dispatch_mutants.sh
#
# The claude CLI only, model sonnet, NO API key (env -u ANTHROPIC_API_KEY; every ANTHROPIC_* variable is removed). W3's cap is
# min(USD 1.50, USD 4.50 - everything spent so far). W3's files are copied byte for byte to h2-mutants-run/ under the names the mutants
# runner reads (MUTANTS.md, m01.diff .. m16.diff). Exit codes: 0 done (next: dotnet_h2_mutants.py --check-inputs, then the inputs manifest),
# 3 refused (nothing called), 4 STOP (a cap, a turn limit, an error or no result: the owner decides), 5 VOID (the transcript audit failed).
set -uo pipefail

DATA2="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DATA2/../../../.." && pwd)"
W_DIR="$HOME/Documents/dn-h2-w"
OUTD="$DATA2/w3"
RUN_DIR="$DATA2/h2-mutants-run"
SPEND="$DATA2/spend.jsonl"
CAP_W3="1.50"
CAP_TOTAL="4.50"
MAX_TURNS=80
MODEL="sonnet"
TOOLS="Bash,Read,Write,Edit,Glob,Grep"
PROMPT='Read BRIEF.md in this directory and do everything it asks. Work only inside this directory.'
PY=python3

refuse() { echo "REFUSED: $*" >&2; exit 3; }

spent() {
  "$PY" - "$SPEND" <<'E'
import json, sys
from pathlib import Path
f = Path(sys.argv[1])
print(f"{sum(float(json.loads(x).get('cost_usd') or 0.0) for x in f.read_text().splitlines() if x.strip()) if f.is_file() else 0.0:.4f}")
E
}

has_line() {
  "$PY" - "$SPEND" "$1" "$2" <<'E'
import json, sys
from pathlib import Path
f, who, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.is_file() else []
sys.exit(0 if any(r.get("who") == who and r.get("phase") == phase for r in rows) else 1)
E
}

record() {
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

unset_args() {
  UNSET=()
  while IFS='=' read -r name _; do
    case "$name" in
      ANTHROPIC_*|CLAUDE_CODE_USE_*|LATTICE_*|CLAUDECODE|CLAUDE_CODE_ENTRYPOINT|CLAUDE_CODE_SSE_PORT|AI_AGENT|ENABLE_IDE_INTEGRATION|PYTHONPATH|VIRTUAL_ENV|DN_REAL_PATH|AWS_BEARER_TOKEN_BEDROCK)
        UNSET+=(-u "$name") ;;
    esac
  done < <(env)
}

transcript_of() {
  local sid="$1" base hit
  for base in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects" "$HOME/.claude/projects"; do
    hit="$(ls -t "$base"/*/"$sid".jsonl 2>/dev/null | head -n 1)"
    if [ -n "$hit" ]; then echo "$hit"; return 0; fi
  done
  return 1
}

# ---- preconditions (nothing is called if one fails) --------------------------------------------------------------------------------------
[ "$(pwd -P)" = "$REPO" ] || refuse "run from the repository root $REPO"
command -v claude >/dev/null || refuse "no claude CLI on PATH"
command -v "$PY" >/dev/null || refuse "no python3 on PATH"
(cd "$REPO" && shasum -a 256 -c --quiet "$DATA2/freeze.sha256") || refuse "the freeze file does not verify: a frozen file changed"
[ -f "$DATA2/h2.tgz" ] && [ -f "$DATA2/h2-truth.json" ] || refuse "H2 is not archived yet (h2.tgz, h2-truth.json)"
"$PY" -c "import json, sys; sys.exit(0 if json.load(open(sys.argv[1])).get('pass') is True else 1)" "$DATA2/author/contract-check.json" 2>/dev/null \
  || refuse "author/contract-check.json does not say \"pass\": true"
has_line W3 dispatch && refuse "W3 was already dispatched (see $SPEND)"
SPENT_ALL="$(spent)"
CAP="$("$PY" -c "import sys; print(f'{min(float(sys.argv[1]), float(sys.argv[2]) - float(sys.argv[3])):.2f}')" "$CAP_W3" "$CAP_TOTAL" "$SPENT_ALL")"
"$PY" -c "import sys; sys.exit(0 if float(sys.argv[1]) >= 0.05 else 1)" "$CAP" || refuse "W3's cap would be USD $CAP (< 0.05): USD $SPENT_ALL already spent of $CAP_TOTAL"
if [ -e "$W_DIR" ] && [ -n "$(ls -A "$W_DIR" 2>/dev/null)" ]; then refuse "$W_DIR exists and is not empty"; fi
if [ -e "$RUN_DIR" ] && [ -n "$(ls -A "$RUN_DIR" 2>/dev/null)" ]; then refuse "$RUN_DIR exists and is not empty"; fi
mkdir -p "$W_DIR" "$OUTD"
case "$(cd "$W_DIR" && pwd -P)" in "$REPO"*) refuse "the W3 directory lies inside the repository" ;; esac
unset_args

# ---- W3's inputs: H2 as archived (its tests/ tree) and the W brief, nothing else ----------------------------------------------------------
tar -xzf "$DATA2/h2.tgz" -C "$W_DIR" || refuse "h2.tgz does not extract"
find "$W_DIR" -name '._*' -delete
cp "$DATA2/brief/BRIEF-W.md" "$W_DIR/BRIEF.md"
MOD_TRUTH="$(ls "$W_DIR"/tests/spec/*/truth.json 2>/dev/null | head -n 1)"
[ -n "$MOD_TRUTH" ] || refuse "no tests/spec/<x>/truth.json in h2.tgz"
cmp -s "$MOD_TRUTH" "$DATA2/h2-truth.json" || refuse "the module's truth.json differs from h2-truth.json"
(cd "$W_DIR" && find . -type f | LC_ALL=C sort | xargs shasum -a 256) > "$OUTD/w3-inputs.sha256"
SID="$(uuidgen | tr 'A-Z' 'a-z')"
echo "$SID" > "$OUTD/w3-session.txt"
claude --version > "$OUTD/w3-claude-version.txt" 2>&1 || true
rc=0
(cd "$W_DIR" && env -u ANTHROPIC_API_KEY ${UNSET[@]+"${UNSET[@]}"} claude -p "$PROMPT" --model "$MODEL" --max-budget-usd "$CAP" \
    --max-turns "$MAX_TURNS" --output-format json --session-id "$SID" --setting-sources "" --strict-mcp-config \
    --disable-slash-commands --tools "$TOOLS" --permission-mode dontAsk --allowedTools "$TOOLS") \
    > "$OUTD/w3-result.json" 2> "$OUTD/w3-stderr.txt" || rc=$?
WHY="$(record W3 dispatch "$SID" "$CAP" "$rc" "$OUTD/w3-result.json")"
ARC=1
if T="$(transcript_of "$SID")"; then
  cp -p "$T" "$OUTD/w3-transcript.jsonl"
  "$PY" "$DATA2/audit_transcript.py" "$OUTD/w3-transcript.jsonl" --root "$W_DIR" --out "$OUTD/w3-audit.json"; ARC=$?
else
  echo "no transcript found for session $SID" >&2
fi
echo "W3: session $SID, cap USD $CAP, spent so far (all) USD $(spent)"
if [ -n "$WHY" ]; then echo "STOP: $WHY. The mutants are not used; nothing is re-dispatched; the owner decides." >&2; exit 4; fi
if [ "$ARC" -ne 0 ]; then echo "VOID: the transcript audit failed (see $OUTD/w3-audit.json): confirm by reading the call; the owner decides." >&2; exit 5; fi

# ---- W3's files, byte for byte, under the names the runner reads (H1 Amendment 6) ----------------------------------------------------------
[ -f "$W_DIR/mutants/MUTANTS.md" ] || { echo "STOP: W3 delivered no mutants/MUTANTS.md; the owner decides." >&2; exit 4; }
mkdir -p "$RUN_DIR"
cp -p "$W_DIR/mutants/MUTANTS.md" "$RUN_DIR/MUTANTS.md" && cmp "$W_DIR/mutants/MUTANTS.md" "$RUN_DIR/MUTANTS.md"
for i in $(seq -w 1 16); do
  if [ -f "$W_DIR/mutants/m$i.diff" ]; then
    cp -p "$W_DIR/mutants/m$i.diff" "$RUN_DIR/m$i.diff" && cmp "$W_DIR/mutants/m$i.diff" "$RUN_DIR/m$i.diff" || { echo "copy of m$i.diff differs" >&2; exit 4; }
  else
    echo "note: no m$i.diff (a NONE line of MUTANTS.md, or missing: --check-inputs decides)"
  fi
done
EXTRA="$(cd "$W_DIR/mutants" && ls -A | grep -v -E '^(MUTANTS\.md|m(0[1-9]|1[0-6])\.diff)$' || true)"
[ -z "$EXTRA" ] || echo "note: W3 left other files in mutants/ (not copied): $EXTRA"
echo "next: python dogfood/dotnet_h2_mutants.py --check-inputs; each patch applies (patch -p1) and builds in a fresh copy; then write and commit h2-inputs.sha256"
exit 0
