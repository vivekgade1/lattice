# Lattice import of `bench.import_repos.taskflow.app`

- LLM loop: on, method M2, model claude-sonnet-5
- routes: 10; verified 6 (60.0%): Lattice R0-R2 6 (60.0%), carve-out R3 0 (0.0%)
- islands R4: 4 (40.0%); needing the LLM: 0 (0.0%)
- by rung: R0 1, R1 4, R2 1, R3 0, R4 4
- original lines by outcome: {'lattice': 62, 'island': 50}; AST nodes: {'lattice': 362, 'island': 276}
- tier mix: T0 6, T1 0, T2 0, T3 0 (islands 4); R3 carve-outs: 0 procedure, 0 native
- cost: $2.0201 (notes $0.0000), 25 LLM rounds, 384640 input / 161656 output tokens, 1237.066 s
- budget: not exhausted (spent $2.0201, 25 rounds)

## Routes

| route | request | status | rung | by | tier | carve | tally eq/rule/dev/mis | rounds | cost | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| register | POST /api/v1/auth/register | verified | R1 | llm | T0 |  | 13/7/0/0 | 1 | $0.0392 | no lifter matches the statements at line 23, 28 |
| login | POST /api/v1/auth/token | island | R4 |  | island |  |  | 7 | $0.8089 | the checker refuses the lifted program |
| read_current_user | GET /api/v1/auth/me | verified | R0 | lifter | T0 |  | 7/0/0/0 | 0 | $0.0000 |  |
| service_info | GET / | island | R4 |  | island |  |  | 0 | $0.0000 | no request suite: defined inside create_app(): the original cannot be referenced for branch coverage |
| health_check | GET /health | island | R4 |  | island |  |  | 0 | $0.0000 | no request suite: defined inside create_app(): the original cannot be referenced for branch coverage |
| create_task | POST /api/v1/tasks | verified | R1 | llm | T0 |  | 17/6/0/0 | 3 | $0.0614 | no lifter matches the statements at line 43 |
| read_tasks | GET /api/v1/tasks | verified | R2 | llm | T0 |  | 15/11/0/0 | 5 | $0.1241 | no lifter matches the statements at line 58, 67 |
| read_task | GET /api/v1/tasks/{task_id} | verified | R1 | llm | T0 |  | 10/1/0/0 | 1 | $0.0103 | no lifter matches the statements at line 74 |
| update_task | PATCH /api/v1/tasks/{task_id} | island | R4 |  | island |  |  | 7 | $0.9650 | verification failed: update_task.partial.title_null: body differs; update_task.partial.status_null: body differs; update_task.partial.priority_null: body differ |
| delete_task | DELETE /api/v1/tasks/{task_id} | verified | R1 | llm | T0 |  | 9/1/0/0 | 1 | $0.0111 | no lifter matches the statements at line 105, 106, 107 |

## Islands

- `login` [missing construct]: the checker refuses the lifted program
- `service_info` [unsupported framework feature: a route defined inside an app factory]: no request suite: defined inside create_app(): the original cannot be referenced for branch coverage
- `health_check` [unsupported framework feature: a route defined inside an app factory]: no request suite: defined inside create_app(): the original cannot be referenced for branch coverage
- `update_task` [behaviour not reproduced]: verification failed: update_task.partial.title_null: body differs; update_task.partial.status_null: body differs; update_task.partial.priority_null: body differs; update_task.hyp.1: body differs; update_task.hyp.2: body differs
