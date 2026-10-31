# Lattice import of `minisvc`

- LLM loop: off (--no-llm)
- routes: 0; verified 0 (0.0%): Lattice R0-R2 0 (0.0%), carve-out R3 0 (0.0%)
- islands R4: 0 (0.0%); needing the LLM: 0 (0.0%)
- by rung: R0 0, R1 0, R2 0, R3 0, R4 0
- original lines by outcome: {}; AST nodes: {}
- tier mix: T0 0, T1 0, T2 0, T3 0 (islands 0); R3 carve-outs: 0 procedure, 0 native
- cost: $0.0000 (notes $0.0000), 0 LLM rounds, 0 input / 0 output tokens, 0.0 s

## Routes

| route | request | status | rung | by | tier | carve | tally eq/rule/dev/mis | rounds | cost | reason |
|---|---|---|---|---|---|---|---|---|---|---|

## Unclear profile decisions

- users_table minisvc: no single current-user dependency names the users table
- auth minisvc: no token-decoding step found in a current-user dependency
- roles minisvc: no superuser dependency: roles_from is not inferred
- storage minisvc: dialect unknown: storage formats are not inferred
- datetime_wire minisvc: custom serialisation or a non-SQLite dialect: the datetime wire is not inferred

## Rule layer (layer R on the original)

- tables verified: 0 of 0; routes verified: 0 of 5; items: verified 5, refuted 0, unexercised 8, unclear 0

| item | verdict | reason |
|---|---|---|
| R1 GET /health | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /notes | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 POST /notes | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R3 POST /notes 422 text must not be blank | unexercised | unreachable-by-probe: no example triggers it |
| R1 GET /notes/{note_id} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R3 GET /notes/{note_id} 404 no such note | unexercised | unreachable-by-probe: the route addresses no row by id |
| R1 DELETE /notes/{note_id} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R3 DELETE /notes/{note_id} 404 no such note | unexercised | unreachable-by-probe: the route addresses no row by id |
