# Pre-registration: Go bare-brief 2×2 (2026-10-09)

**Question.** Does Lattice (the spec plus the gate loop) help in Go when the briefs carry *no* implementation advice?
The matched-brief run tied (spec-first 388k tokens, PASS; baseline 390k, PASS), because the briefs already held the lessons Lattice had found.

**Arms.** One build per cell, 5 tickets, one fresh agent per ticket. Every repo starts from the same Gin + GORM skeleton (job-scheduler-go@9d8976b).

| | prose `REQUIREMENTS.md`, gate `go build`/`go test`/`go vet` | Lattice spec, gate `./check.sh N` (ABCDX, lattice bbb6185) |
|---|---|---|
| Opus | job-scheduler-go-min-base-opus | job-scheduler-go-min-spec-opus |
| Sonnet | job-scheduler-go-min-base-sonnet | job-scheduler-go-min-spec-sonnet |

**Briefs.** The same bare template for every arm: build ticket N as the requirements or the spec state it, with tests, the hard rules and the gate. There is no implementation advice: no concurrency patterns, no indexes, no process groups, no password limits beyond what the requirements or the spec themselves say.

**Primary measure.** After ticket 5, the blind judge findings with the same unchanged spec. The judge runs BCDX at lattice 11a7032 (as for every earlier arm), plus `--x-repeats 6 --x-budget 200` and the 4 HTTP race probes. Layer A (gocheck, bbb6185) is reported separately.

**Secondary measures.** Agent tokens, tool calls, wall time, rounds, lines of code, and orchestrator interventions (none planned; a stuck agent is recorded, not helped).

**Expectation, stated in advance.** For each model, the spec arm ends with fewer judge findings than the prose arm. The gap is larger for Sonnet than for Opus. The spec arm costs more tokens. With n = 1 per cell this is descriptive, not a test.
