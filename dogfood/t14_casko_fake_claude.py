#!/usr/bin/env python3
# ruff: noqa: E501
"""A fake ``claude`` binary for the T1-4 dry run: no model, no network, cost from the environment (default 0).

It answers ``claude -p --output-format json`` like the CLI: one JSON result whose ``result`` is the scripted ask() reply of the question
named on the prompt's ``Question:`` line, from dogfood/t14_casko_questions.json:

* ``T14_FAKE_MODE=gold``: the question's gold reply (its citations are the key's evidence);
* ``T14_FAKE_MODE=wrongcite``: the gold text with wrong citations: an ``impl:route:`` id that does not resolve and one unrelated, valid
  invariant (so the library keeps the answer and the route and rule claims are left uncited).

``T14_FAKE_COST``: the ``total_cost_usd`` reported; ``T14_FAKE_MARK``: a file each call's argv is appended to (tests).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

QUESTIONS = Path(__file__).resolve().parent / "t14_casko_questions.json"
WRONG_CITES = [
    {"target": "impl:route:GET /v1/does-not-exist", "step": None, "quote": "registered code:nowhere.go:1"},
    {"target": "invariant:one_account_per_email", "step": None, "quote": "An email is registered at most once"},
]


def reply(question: str, mode: str) -> str:
    qs = json.loads(Path(os.environ.get("T14_FAKE_QUESTIONS") or QUESTIONS).read_text())["questions"]
    gold = next((q["gold_reply"] for q in qs if q["question"] == question), None)
    if gold is None:
        return json.dumps({"kind": "not_specified", "answer": "Not specified.", "explanation": "unknown question", "citations": []})
    if mode == "wrongcite":
        gold = {**gold, "citations": WRONG_CITES}
    return json.dumps(gold)


def main() -> int:
    argv = sys.argv[1:]
    mark = os.environ.get("T14_FAKE_MARK")
    if mark:
        Path(mark).parent.mkdir(parents=True, exist_ok=True)
        with open(mark, "a") as f:
            f.write(json.dumps(argv) + "\n")
    if "--version" in argv:
        print("0.0.0 (fake claude, T1-4 dry run)")
        return 0
    prompt = sys.stdin.read()
    m = re.search(r"^Question: (.+)$", prompt, re.MULTILINE)
    text = reply(m.group(1).strip() if m else "", os.environ.get("T14_FAKE_MODE", "gold"))
    cost = float(os.environ.get("T14_FAKE_COST") or 0.0)
    print(json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": text, "stop_reason": "end_turn",
        "total_cost_usd": cost, "num_turns": 1, "session_id": "fake", "duration_ms": 1,
        "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "modelUsage": {"fake": {"costUSD": cost}},
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
