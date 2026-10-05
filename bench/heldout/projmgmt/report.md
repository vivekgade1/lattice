# Lattice import of `bench.import_repos.projmgmt`

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

- private Project: no response model returns Project; its private columns are not inferred
- owner Project: no route filters, checks or sets Project.user_id from the caller
- private Task: no response model returns Task; its private columns are not inferred
- private User: no response model returns User; its private columns are not inferred
- auth User: the app's own auth (flask, bearer) is not lifted: the profile's NativeToken names caller_key, which refuses to run (lattice.importer.app_auth)
- error_body bench.import_repos.projmgmt: custom exception handlers change the error body

## Rule layer (layer R on the original)

- tables verified: 1 of 3; routes verified: 23 of 23; items: verified 50, refuted 1, unexercised 65, unclear 1, verified (corrected) 8

| item | verdict | reason |
|---|---|---|
| T1 Project | unclear | the owner is unclear: no route filters, checks or sets Project.user_id from the caller |
| T1 User | unexercised | no probe reached it |
| T4 User.user_type | refuted | stored values outside the list: ['employee', 'manager'] |
| T5 Project (id) | unexercised | no route can repeat the key: no create route of Project takes (id) or stamps it from the caller |
| T5 Task (id) | unexercised | no route creates Task rows |
| T5 User (id) | unexercised | no route can repeat the key: no create route of User takes (id) or stamps it from the caller |
| R3 POST /auth/login 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /auth/login 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /auth/login 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /auth/register 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /auth/register 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /auth/refresh 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /auth/refresh 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /auth/refresh 404 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /auth/refresh 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /auth/logout 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /auth/verify 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /auth/verify 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /users 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /users 409 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /users 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /users 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /users 403 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /users 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /users 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /users 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /users/{user_id} 404 | verified (corrected) | corrected (404 None -> 401 None); answered 401 None; stated 401 None |
| R3 GET /users/{user_id} 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /users/{user_id} 404 | verified (corrected) | corrected (404 None -> 401 None); answered 401 None; stated 401 None |
| R3 PUT /users/{user_id} 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /users/{user_id} 409 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /users/{user_id} 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /users/{user_id} 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /users/{user_id} 403 | unexercised | unreachable-by-probe: no example triggers it |
| R3 DELETE /users/{user_id} 404 | verified (corrected) | corrected (404 None -> 401 None); answered 401 None; stated 401 None |
| R3 DELETE /users/{user_id} 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 DELETE /users/{user_id} 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 DELETE /users/{user_id} 403 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /projects 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /projects 404 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /projects 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /projects 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /projects 403 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /projects 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /projects/{project_id} 404 | verified (corrected) | corrected (404 None -> 401 None); answered 401 None; stated 401 None |
| R3 PUT /projects/{project_id} 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /projects/{project_id} 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /projects/{project_id} 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /projects/{project_id} 403 | unexercised | unreachable-by-probe: no example triggers it |
| R3 DELETE /projects/{project_id} 404 | verified (corrected) | corrected (404 None -> 401 None); answered 401 None; stated 401 None |
| R3 DELETE /projects/{project_id} 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 DELETE /projects/{project_id} 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 DELETE /projects/{project_id} 403 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /tasks 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /tasks 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /tasks 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /tasks 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /tasks 404 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /tasks 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /tasks 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /projects/{project_id}/tasks 404 | verified (corrected) | corrected (404 None -> 401 None); answered 401 None; stated 401 None |
| R3 POST /projects/{project_id}/tasks 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /projects/{project_id}/tasks 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /projects/{project_id}/tasks 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /projects/{project_id}/tasks 403 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /projects/{project_id}/tasks 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 GET /projects/{project_id}/tasks 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /tasks/{task_id} 404 | verified (corrected) | corrected (404 None -> 401 None); answered 401 None; stated 401 None |
| R3 PUT /tasks/{task_id} 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /tasks/{task_id} 404 #2 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /tasks/{task_id} 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /tasks/{task_id} 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 DELETE /tasks/{task_id} 404 | verified (corrected) | corrected (404 None -> 401 None); answered 401 None; stated 401 None |
| R3 DELETE /tasks/{task_id} 500 | unexercised | unreachable-by-probe: no example triggers it |
| R3 DELETE /tasks/{task_id} 401 | unexercised | unreachable-by-probe: no example triggers it |
