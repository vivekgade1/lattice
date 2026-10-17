# Change impact probes (Streak, dev stage)

- RAN PX: PASS -- wall time on S: T2+T3; T1 (2 ACD runs + 59 flows): observed {"t1_full_s": 70.3, "t2_t3_s": 0.1}
- RAN PW: PASS -- no write: snapshots around the CLI x10, the MCP tool x10 (stdio, --readonly, observe, plus one non-readonly observe), with a recording claude shim: observed {"changed": 0, "diffs": {}, "mcp": {"observe_refused": 10, "ok": 20}, "new": 0, "observe_ok": true, "rcs": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], "shim_calls": []}
- RAN LEAK: PASS -- containers owned by this work after a fault run and an MCP timeout: observed {"before": [], "fault": {"error": "RuntimeError: injected fault in the candidate's check", "left": [], "rc": 3}, "left_after_timeout": [], "mcp_timeout_reported": true}
