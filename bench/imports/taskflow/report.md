# Lattice import of `bench.import_repos.taskflow.app`

- LLM loop: on, method M2, model claude-sonnet-5
- routes: 10; verified 5 (50.0%): Lattice R0-R2 5 (50.0%), carve-out R3 0 (0.0%)
- islands R4: 5 (50.0%); needing the LLM: 0 (0.0%)
- by rung: R0 1, R1 4, R2 0, R3 0, R4 5
- original lines by outcome: {'lattice': 51, 'island': 61}; AST nodes: {'lattice': 303, 'island': 335}
- tier mix: T0 5, T1 0, T2 0, T3 0 (islands 5); R3 carve-outs: 0 procedure, 0 native
- cost: $2.9390 (notes $0.0000), 33 LLM rounds, 472833 input / 244252 output tokens, 2656.794 s
- budget: not exhausted (spent $2.9390, 33 rounds)

## Routes

| route | request | status | rung | by | tier | carve | tally eq/rule/dev/mis | rounds | cost | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| register | POST /api/v1/auth/register | verified | R1 | llm | T0 |  | 13/7/0/0 | 3 | $0.0877 | no lifter matches the statements at line 23, 28 |
| login | POST /api/v1/auth/token | island | R4 |  | island |  |  | 7 | $1.0030 | cut off |
| read_current_user | GET /api/v1/auth/me | verified | R0 | lifter | T0 |  | 4/0/3/0 | 0 | $0.0000 |  |
| service_info | GET / | island | R4 |  | island |  |  | 0 | $0.0000 | no request suite: defined inside create_app(): the original cannot be referenced for branch coverage |
| health_check | GET /health | island | R4 |  | island |  |  | 0 | $0.0000 | no request suite: defined inside create_app(): the original cannot be referenced for branch coverage |
| create_task | POST /api/v1/tasks | verified | R1 | llm | T0 |  | 14/6/3/0 | 4 | $0.1224 | no lifter matches the statements at line 43 |
| read_tasks | GET /api/v1/tasks | verified | R1 | llm | T0 |  | 12/11/3/0 | 3 | $0.0731 | no lifter matches the statements at line 58, 67 |
| read_task | GET /api/v1/tasks/{task_id} | verified | R1 | llm | T0 |  | 7/1/3/0 | 2 | $0.0195 | no lifter matches the statements at line 74 |
| update_task | PATCH /api/v1/tasks/{task_id} | island | R4 |  | island |  |  | 7 | $0.8111 | cut off |
| delete_task | DELETE /api/v1/tasks/{task_id} | island | R4 |  | island |  |  | 7 | $0.8222 | cut off |

## Behaviour changes (need human acceptance)

- `read_current_user` invalid-token-401 (1 cases): The original answers an invalid bearer token with 401 'Could not validate credentials'; Lattice's auth answers every missing or invalid credential with 401.
- `read_current_user` unknown-user-token (1 cases): A valid token naming a user that does not exist: the original answers 401 'Could not validate credentials'; Lattice's token auth answers 401 like any token naming no caller.
- `read_current_user` claimless-token (1 cases): A valid token without the caller's claim: the original answers 401 'Could not validate credentials'; Lattice's token auth answers 401 like any token naming no caller.
- `create_task` invalid-token-401 (1 cases): The original answers an invalid bearer token with 401 'Could not validate credentials'; Lattice's auth answers every missing or invalid credential with 401.
- `create_task` unknown-user-token (1 cases): A valid token naming a user that does not exist: the original answers 401 'Could not validate credentials'; Lattice's token auth answers 401 like any token naming no caller.
- `create_task` claimless-token (1 cases): A valid token without the caller's claim: the original answers 401 'Could not validate credentials'; Lattice's token auth answers 401 like any token naming no caller.
- `read_tasks` invalid-token-401 (1 cases): The original answers an invalid bearer token with 401 'Could not validate credentials'; Lattice's auth answers every missing or invalid credential with 401.
- `read_tasks` unknown-user-token (1 cases): A valid token naming a user that does not exist: the original answers 401 'Could not validate credentials'; Lattice's token auth answers 401 like any token naming no caller.
- `read_tasks` claimless-token (1 cases): A valid token without the caller's claim: the original answers 401 'Could not validate credentials'; Lattice's token auth answers 401 like any token naming no caller.
- `read_task` invalid-token-401 (1 cases): The original answers an invalid bearer token with 401 'Could not validate credentials'; Lattice's auth answers every missing or invalid credential with 401.
- `read_task` unknown-user-token (1 cases): A valid token naming a user that does not exist: the original answers 401 'Could not validate credentials'; Lattice's token auth answers 401 like any token naming no caller.
- `read_task` claimless-token (1 cases): A valid token without the caller's claim: the original answers 401 'Could not validate credentials'; Lattice's token auth answers 401 like any token naming no caller.

## Islands

- `login` [missing construct]: cut off
- `service_info` [unsupported framework feature: a route defined inside an app factory]: no request suite: defined inside create_app(): the original cannot be referenced for branch coverage
- `health_check` [unsupported framework feature: a route defined inside an app factory]: no request suite: defined inside create_app(): the original cannot be referenced for branch coverage
- `update_task` [missing construct]: cut off
- `delete_task` [missing construct]: cut off

## Unclear profile decisions

- owner Task: non-admin routes create_task, delete_task, read_task, update_task touch Task without comparing owner_id to the caller
