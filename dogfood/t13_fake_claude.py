#!/usr/bin/env python3
"""A fake ``claude`` for the T1-3 dry run: no model, no network.

Called by ``t13_shim.py`` in place of the real binary. For a ``-p`` call it logs its argv and environment names to
``$FAKE_T13_LOG`` (JSONL), writes the files of ``$FAKE_T13_PLAN`` (``{"files": {path: text | null}, "cost": usd}``,
paths relative to its cwd, the agent's worktree; ``null`` deletes) and prints the stream-json records a rebuild step
reads: ``system/init``, one assistant text and a ``result`` with ``total_cost_usd`` = the plan's cost (default 0).
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SID = "t13-fake-0000-0000-0000-000000000000"


def main() -> int:
    argv = sys.argv[1:]
    if "-p" not in argv and "--print" not in argv:
        print("0.0.0 (t13 fake claude)")
        return 0
    log = os.environ.get("FAKE_T13_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps({"argv": argv, "env": sorted(os.environ), "cwd": os.getcwd()}) + "\n")
    plan = json.loads(Path(os.environ["FAKE_T13_PLAN"]).read_text()) if os.environ.get("FAKE_T13_PLAN") else {}
    for rel, text in (plan.get("files") or {}).items():
        f = Path.cwd() / rel
        if text is None:
            f.unlink(missing_ok=True)
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    summary = plan.get("summary") or "fake agent: wrote the planned files"
    print(json.dumps({"type": "system", "subtype": "init", "session_id": SID}), flush=True)
    print(json.dumps({"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": summary}]}}), flush=True)
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": summary, "session_id": SID,
                      "total_cost_usd": float(plan.get("cost") or 0.0), "duration_ms": 1, "num_turns": 1,
                      "usage": {"input_tokens": 0, "output_tokens": 0}}), flush=True)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
