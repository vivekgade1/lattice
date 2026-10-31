# Lattice import of `cyclops.ui`

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

- users_table cyclops.ui: no single current-user dependency names the users table
- auth cyclops.ui: no token-decoding step found in a current-user dependency
- roles cyclops.ui: no superuser dependency: roles_from is not inferred
- storage cyclops.ui: dialect unknown: storage formats are not inferred
- datetime_wire cyclops.ui: custom serialisation or a non-SQLite dialect: the datetime wire is not inferred

## Rule layer (layer R on the original)

- tables verified: 0 of 0; routes verified: 0 of 31; items: verified 36, refuted 0, unexercised 31, unclear 0

| item | verdict | reason |
|---|---|---|
| R1 GET /api/roots | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/quotes | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/meta | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/bars/{rung} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/bars/{rung}/windows | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/features/{rung} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/regimes | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/bar/{ts} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/patterns/{rung} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/patterns/{rung}/similar | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/structure/{rung} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/formations/{rung} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/bands/{rung} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/memory/{ts} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/profiles | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/validation/{split} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/dq | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/eod | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/eod/{d} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/journal/{d} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/rolling | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/registry | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/parity | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/live | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/user/drawings/{rung} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 PUT /api/user/drawings/{rung} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/user/setups | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/user/setups/{slug} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 PUT /api/user/setups/{slug} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 DELETE /api/user/setups/{slug} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /{path:path} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
