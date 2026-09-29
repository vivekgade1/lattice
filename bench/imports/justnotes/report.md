# Lattice import of `bench.import_repos.justnotes.app`

- LLM loop: on, method M2, model claude-sonnet-5
- routes: 30; verified 10 (33.3%): Lattice R0-R2 10 (33.3%), carve-out R3 0 (0.0%)
- islands R4: 20 (66.7%); needing the LLM: 0 (0.0%)
- by rung: R0 1, R1 9, R2 0, R3 0, R4 20
- original lines by outcome: {'island': 598, 'lattice': 254}; AST nodes: {'island': 1575, 'lattice': 683}
- tier mix: T0 10, T1 0, T2 0, T3 0 (islands 20); R3 carve-outs: 0 procedure, 0 native
- cost: $6.0148 (notes $0.0000), 95 LLM rounds, 1639286 input / 416269 output tokens, 3298.094 s
- budget: not exhausted (spent $6.0148, 95 rounds)

## Routes

| route | request | status | rung | by | tier | carve | tally eq/rule/dev/mis | rounds | cost | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| register | POST /auth/register | island | R4 |  | island |  |  | 7 | $2.2739 | verification failed: register.happy: database effects differ; register.extra.unknown: database effects differ; register.extra.columns: database effects differ;  |
| login | POST /auth/login | island | R4 |  | island |  |  | 6 | $0.1499 | budget: the route's share of the repo budget ($0.14) is reached |
| read_users_me | GET /auth/me | verified | R0 | lifter | T0 |  | 6/0/0/0 | 0 | $0.0000 |  |
| health | GET /health | verified | R1 | llm | T0 |  | 1/0/0/0 | 3 | $0.0476 | no lifter matches the statements at line 9 |
| get_notes | GET /notes | island | R4 |  | island |  |  | 6 | $0.1629 | budget: the route's share of the repo budget ($0.14) is reached |
| create_notes | POST /notes/create | island | R4 |  | island |  |  | 5 | $0.1837 | budget: the route's share of the repo budget ($0.15) is reached |
| get_public_notes | GET /notes/public | island | R4 |  | island |  |  | 6 | $0.1447 | budget: the route's share of the repo budget ($0.14) is reached |
| get_notes_summarize | GET /notes/{id}/summarize | verified | R1 | llm | T0 |  | 7/1/0/0 | 3 | $0.0903 | no lifter matches the statements at line 184, 185, 187, 194 |
| get_notes_extend | GET /notes/{id}/extend | island | R4 |  | island |  |  | 3 | $0.0867 | budget: the route's share of the repo budget ($0.08) is reached |
| get_notes_translate | GET /notes/{id}/translate/{target_language} | island | R4 |  | island |  |  | 2 | $0.0575 | budget: the route's share of the repo budget ($0.05) is reached |
| get_notes_auto_tag | GET /notes/{id}/auto-tag | island | R4 |  | island |  |  | 3 | $0.0920 | budget: the route's share of the repo budget ($0.06) is reached |
| get_notes_keywords | GET /notes/{id}/keywords | island | R4 |  | island |  |  | 3 | $0.0885 | budget: the route's share of the repo budget ($0.07) is reached |
| get_notes_sentiment | GET /notes/{id}/sentiment | island | R4 |  | island |  |  | 2 | $0.0533 | budget: the route's share of the repo budget ($0.05) is reached |
| get_notes_related | GET /notes/{id}/related | verified | R1 | llm | T0 |  | 7/1/0/0 | 2 | $0.0669 | no lifter matches the statements at line 415, 416, 418, 420, 426, 427, 434, 460 |
| get_note | GET /notes/{id} | verified | R1 | llm | T0 |  | 7/1/0/0 | 3 | $0.0927 | no lifter matches the statements at line 484, 485, 487, 494, 503 |
| update_note | PUT /notes/{id} | island | R4 |  | island |  |  | 1 | $1.0909 | the checker refuses the lifted program |
| delete_note | DELETE /notes/{id} | verified | R1 | llm | T0 |  | 6/1/0/0 | 1 | $0.0209 | no lifter matches the statements at line 559 |
| get_user_tags | GET /tags | island | R4 |  | island |  |  | 7 | $0.3837 | the checker refuses the lifted program |
| create_tag | POST /tags | island | R4 |  | island |  |  | 5 | $0.1758 | budget: the route's share of the repo budget ($0.14) is reached |
| delete_tag | DELETE /tags/{tag_id} | verified | R1 | llm | T0 |  | 8/1/0/0 | 1 | $0.0194 | no lifter matches the statements at line 138, 139 |
| add_tag_to_note | POST /notes/{note_id}/tags/{tag_id} | island | R4 |  | island |  |  | 2 | $0.0598 | unverified branch: Verification against the original failed: on every case the endpoint must answer the original's status and JSON body and leave the same datab |
| remove_tag_from_note | DELETE /notes/{note_id}/tags/{tag_id} | island | R4 |  | island |  |  | 2 | $0.0541 | unverified branch: Verification against the original failed: on every case the endpoint must answer the original's status and JSON body and leave the same datab |
| get_note_tags | GET /notes/{note_id}/tags | island | R4 |  | island |  |  | 3 | $0.0937 | budget: the route's share of the repo budget ($0.08) is reached |
| get_user_keywords | GET /keywords | verified | R1 | llm | T0 |  | 6/0/0/0 | 3 | $0.0564 | no lifter matches the statements at line 265, 266 |
| create_keyword | POST /keywords | island | R4 |  | island |  |  | 5 | $0.1737 | budget: the route's share of the repo budget ($0.14) is reached |
| delete_keyword | DELETE /keywords/{keyword_id} | verified | R1 | llm | T0 |  | 8/1/0/0 | 1 | $0.0210 | no lifter matches the statements at line 325, 326 |
| add_keyword_to_note | POST /notes/{note_id}/keywords/{keyword_id} | island | R4 |  | island |  |  | 2 | $0.0588 | unverified branch: Verification against the original failed: on every case the endpoint must answer the original's status and JSON body and leave the same datab |
| remove_keyword_from_note | DELETE /notes/{note_id}/keywords/{keyword_id} | island | R4 |  | island |  |  | 2 | $0.0560 | unverified branch: Verification against the original failed: on every case the endpoint must answer the original's status and JSON body and leave the same datab |
| get_note_keywords | GET /notes/{note_id}/keywords | island | R4 |  | island |  |  | 3 | $0.1070 | budget: the route's share of the repo budget ($0.08) is reached |
| list_users | GET /user/list | verified | R1 | llm | T0 |  | 6/0/0/0 | 3 | $0.0530 | no lifter matches the statements at line 30 |

## Islands

- `register` [behaviour not reproduced]: verification failed: register.happy: database effects differ; register.extra.unknown: database effects differ; register.extra.columns: database effects differ; register.hyp.0: database effects differ; register.hyp.1: database effects differ; register.hyp.2: database effects differ; register.hyp.3: database effects differ; register.hyp.4: database effects differ
- `login` [budget]: budget: the route's share of the repo budget ($0.14) is reached
- `get_notes` [budget]: budget: the route's share of the repo budget ($0.14) is reached
- `create_notes` [budget]: budget: the route's share of the repo budget ($0.15) is reached
- `get_public_notes` [budget]: budget: the route's share of the repo budget ($0.14) is reached
- `get_notes_extend` [budget]: budget: the route's share of the repo budget ($0.08) is reached
- `get_notes_translate` [budget]: budget: the route's share of the repo budget ($0.05) is reached
- `get_notes_auto_tag` [budget]: budget: the route's share of the repo budget ($0.06) is reached
- `get_notes_keywords` [budget]: budget: the route's share of the repo budget ($0.07) is reached
- `get_notes_sentiment` [budget]: budget: the route's share of the repo budget ($0.05) is reached
- `update_note` [unsupported framework feature: dynamic dispatch]: the checker refuses the lifted program
- `get_user_tags` [missing construct]: the checker refuses the lifted program
- `create_tag` [budget]: budget: the route's share of the repo budget ($0.14) is reached
- `add_tag_to_note` [unverifiable: a raise site no case reaches]: unverified branch: Verification against the original failed: on every case the endpoint must answer the original's status and JSON body and leave the same database rows. - the original's branch add_tag_to_note (tags.py:168) -> 403 was reached by no case
- `remove_tag_from_note` [unverifiable: a raise site no case reaches]: unverified branch: Verification against the original failed: on every case the endpoint must answer the original's status and JSON body and leave the same database rows. - the original's branch remove_tag_from_note (tags.py:202) -> 403 was reached by no case
- `get_note_tags` [budget]: budget: the route's share of the repo budget ($0.08) is reached
- `create_keyword` [budget]: budget: the route's share of the repo budget ($0.14) is reached
- `add_keyword_to_note` [unverifiable: a raise site no case reaches]: unverified branch: Verification against the original failed: on every case the endpoint must answer the original's status and JSON body and leave the same database rows. - the original's branch add_keyword_to_note (tags.py:355) -> 403 was reached by no case
- `remove_keyword_from_note` [unverifiable: a raise site no case reaches]: unverified branch: Verification against the original failed: on every case the endpoint must answer the original's status and JSON body and leave the same database rows. - the original's branch remove_keyword_from_note (tags.py:389) -> 403 was reached by no case
- `get_note_keywords` [budget]: budget: the route's share of the repo budget ($0.08) is reached

## Unclear profile decisions

- private NoteTagLink: no response model returns NoteTagLink; its private columns are not inferred
- private Tag: no response model returns Tag; its private columns are not inferred
- private NoteKeywordLink: no response model returns NoteKeywordLink; its private columns are not inferred
- private Keyword: no response model returns Keyword; its private columns are not inferred
- auth User: the token's 'sub' claim names User.username, which is not declared unique (the app may enforce it in code only): the lookup is checked per request (NativeToken unique="runtime"): no row answers 401, several a loud 500 (R311, an ambiguous caller)
