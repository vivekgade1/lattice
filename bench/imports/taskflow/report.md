# Lattice import of `bench.import_repos.taskflow.app`

- LLM loop: on, method M2, model claude-sonnet-5
- routes: 10; verified 6 (60.0%): Lattice R0-R2 6 (60.0%), carve-out R3 0 (0.0%)
- islands R4: 4 (40.0%); needing the LLM: 0 (0.0%)
- by rung: R0 1, R1 4, R2 1, R3 0, R4 4
- original lines by outcome: {'lattice': 62, 'island': 50}; AST nodes: {'lattice': 362, 'island': 276}
- tier mix: T0 6, T1 0, T2 0, T3 0 (islands 4); R3 carve-outs: 0 procedure, 0 native
- cost: $1.4551 (notes $0.0000), 26 LLM rounds, 405861 input / 106196 output tokens, 1158.139 s
- budget: not exhausted (spent $1.4551, 26 rounds)

## Routes

| route | request | status | rung | by | tier | carve | tally eq/rule/dev/mis | rounds | cost | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| register | POST /api/v1/auth/register | verified | R1 | llm | T0 |  | 13/7/0/0 | 3 | $0.0652 | no lifter matches the statements at line 23, 28 |
| login | POST /api/v1/auth/token | island | R4 |  | island |  |  | 4 | $0.4599 | budget: the route's share of the repo budget ($0.29) is reached |
| read_current_user | GET /api/v1/auth/me | verified | R0 | lifter | T0 |  | 7/0/0/0 | 0 | $0.0000 |  |
| service_info | GET / | island | R4 |  | island |  |  | 0 | $0.0000 | no request suite: defined inside create_app(): the original cannot be referenced for branch coverage |
| health_check | GET /health | island | R4 |  | island |  |  | 0 | $0.0000 | no request suite: defined inside create_app(): the original cannot be referenced for branch coverage |
| create_task | POST /api/v1/tasks | verified | R2 | llm | T0 |  | 17/6/0/0 | 6 | $0.1777 | no lifter matches the statements at line 43 |
| read_tasks | GET /api/v1/tasks | verified | R1 | llm | T0 |  | 15/11/0/0 | 2 | $0.0271 | no lifter matches the statements at line 58, 67 |
| read_task | GET /api/v1/tasks/{task_id} | verified | R1 | llm | T0 |  | 10/1/0/0 | 2 | $0.0180 | no lifter matches the statements at line 74 |
| update_task | PATCH /api/v1/tasks/{task_id} | island | R4 |  | island |  |  | 7 | $0.6885 | cut off |
| delete_task | DELETE /api/v1/tasks/{task_id} | verified | R1 | llm | T0 |  | 9/1/0/0 | 2 | $0.0187 | no lifter matches the statements at line 105, 106, 107 |

## Islands

- `login` [budget]: budget: the route's share of the repo budget ($0.29) is reached
- `service_info` [unsupported framework feature: a route defined inside an app factory]: no request suite: defined inside create_app(): the original cannot be referenced for branch coverage
- `health_check` [unsupported framework feature: a route defined inside an app factory]: no request suite: defined inside create_app(): the original cannot be referenced for branch coverage
- `update_task` [missing construct]: cut off

## Unclear profile decisions

- owner Task: non-admin routes create_task, delete_task, read_task, update_task touch Task without comparing owner_id to the caller
