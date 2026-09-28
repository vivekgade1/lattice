# Lattice import of `bench.import_repos.justnotes.app`

- LLM loop: on, method M2, model claude-sonnet-5
- routes: 30; verified 6 (20.0%): Lattice R0-R2 6 (20.0%), carve-out R3 0 (0.0%)
- islands R4: 24 (80.0%); needing the LLM: 0 (0.0%)
- by rung: R0 1, R1 5, R2 0, R3 0, R4 24
- original lines by outcome: {'island': 746, 'lattice': 106}; AST nodes: {'island': 1986, 'lattice': 272}
- tier mix: T0 6, T1 0, T2 0, T3 0 (islands 24); R3 carve-outs: 0 procedure, 0 native
- cost: $6.5688 (notes $0.0000), 98 LLM rounds, 1524867 input / 510247 output tokens, 6004.598 s
- budget: exhausted -- the repo's cost cap ($6.50) is reached (spent $6.5688, 98 rounds)

## Routes

| route | request | status | rung | by | tier | carve | tally eq/rule/dev/mis | rounds | cost | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| register | POST /auth/register | island | R4 |  | island |  |  | 5 | $0.3267 | budget: the route's share of the repo budget ($0.23) is reached |
| login | POST /auth/login | island | R4 |  | island |  |  | 2 | $0.2337 | budget: the route's share of the repo budget ($0.22) is reached |
| read_users_me | GET /auth/me | verified | R0 | lifter | T0 |  | 6/0/0/0 | 0 | $0.0000 |  |
| health | GET /health | verified | R1 | llm | T0 |  | 1/0/0/0 | 2 | $0.0465 | no lifter matches the statements at line 9 |
| get_notes | GET /notes | island | R4 |  | island |  |  | 4 | $0.2684 | budget: the route's share of the repo budget ($0.22) is reached |
| create_notes | POST /notes/create | island | R4 |  | island |  |  | 4 | $0.2525 | budget: the route's share of the repo budget ($0.22) is reached |
| get_public_notes | GET /notes/public | island | R4 |  | island |  |  | 3 | $0.3714 | budget: the route's share of the repo budget ($0.23) is reached |
| get_notes_summarize | GET /notes/{id}/summarize | island | R4 |  | island |  |  | 6 | $0.5018 | budget: the route's share of the repo budget ($0.18) is reached |
| get_notes_extend | GET /notes/{id}/extend | verified | R1 | llm | T0 |  | 7/1/0/0 | 4 | $0.1570 | no lifter matches the statements at line 203, 204, 206, 214 |
| get_notes_translate | GET /notes/{id}/translate/{target_language} | island | R4 |  | island |  |  | 4 | $0.3642 | budget: the repo's cost cap ($6.50) is reached |
| get_notes_auto_tag | GET /notes/{id}/auto-tag | island | R4 |  | island |  |  | 4 | $0.1059 | budget: the route's share of the repo budget ($0.10) is reached |
| get_notes_keywords | GET /notes/{id}/keywords | island | R4 |  | island |  |  | 4 | $0.1003 | budget: the route's share of the repo budget ($0.10) is reached |
| get_notes_sentiment | GET /notes/{id}/sentiment | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.50) is reached |
| get_notes_related | GET /notes/{id}/related | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.50) is reached |
| get_note | GET /notes/{id} | island | R4 |  | island |  |  | 3 | $0.2630 | budget: the route's share of the repo budget ($0.22) is reached |
| update_note | PUT /notes/{id} | island | R4 |  | island |  |  | 1 | $0.3559 | cut off |
| delete_note | DELETE /notes/{id} | verified | R1 | llm | T0 |  | 6/1/0/0 | 3 | $0.1492 | no lifter matches the statements at line 559 |
| get_user_tags | GET /tags | island | R4 |  | island |  |  | 4 | $0.3057 | budget: the route's share of the repo budget ($0.22) is reached |
| create_tag | POST /tags | island | R4 |  | island |  |  | 5 | $0.3072 | budget: the route's share of the repo budget ($0.22) is reached |
| delete_tag | DELETE /tags/{tag_id} | verified | R1 | llm | T0 |  | 8/1/0/0 | 3 | $0.0630 | no lifter matches the statements at line 138, 139 |
| add_tag_to_note | POST /notes/{note_id}/tags/{tag_id} | island | R4 |  | island |  |  | 4 | $0.3011 | budget: the route's share of the repo budget ($0.20) is reached |
| remove_tag_from_note | DELETE /notes/{note_id}/tags/{tag_id} | island | R4 |  | island |  |  | 4 | $0.2039 | budget: the route's share of the repo budget ($0.20) is reached |
| get_note_tags | GET /notes/{note_id}/tags | island | R4 |  | island |  |  | 5 | $0.2233 | budget: the route's share of the repo budget ($0.14) is reached |
| get_user_keywords | GET /keywords | island | R4 |  | island |  |  | 3 | $0.3881 | budget: the route's share of the repo budget ($0.22) is reached |
| create_keyword | POST /keywords | island | R4 |  | island |  |  | 4 | $0.2616 | budget: the route's share of the repo budget ($0.22) is reached |
| delete_keyword | DELETE /keywords/{keyword_id} | verified | R1 | llm | T0 |  | 8/1/0/0 | 3 | $0.0440 | no lifter matches the statements at line 325, 326 |
| add_keyword_to_note | POST /notes/{note_id}/keywords/{keyword_id} | island | R4 |  | island |  |  | 4 | $0.1973 | budget: the route's share of the repo budget ($0.18) is reached |
| remove_keyword_from_note | DELETE /notes/{note_id}/keywords/{keyword_id} | island | R4 |  | island |  |  | 3 | $0.3068 | budget: the route's share of the repo budget ($0.19) is reached |
| get_note_keywords | GET /notes/{note_id}/keywords | island | R4 |  | island |  |  | 4 | $0.2423 | budget: the route's share of the repo budget ($0.12) is reached |
| list_users | GET /user/list | island | R4 |  | island |  |  | 3 | $0.2282 | budget: the route's share of the repo budget ($0.22) is reached |

## Islands

- `register` [budget]: budget: the route's share of the repo budget ($0.23) is reached
- `login` [budget]: budget: the route's share of the repo budget ($0.22) is reached
- `get_notes` [budget]: budget: the route's share of the repo budget ($0.22) is reached
- `create_notes` [budget]: budget: the route's share of the repo budget ($0.22) is reached
- `get_public_notes` [budget]: budget: the route's share of the repo budget ($0.23) is reached
- `get_notes_summarize` [budget]: budget: the route's share of the repo budget ($0.18) is reached
- `get_notes_translate` [budget]: budget: the repo's cost cap ($6.50) is reached
- `get_notes_auto_tag` [budget]: budget: the route's share of the repo budget ($0.10) is reached
- `get_notes_keywords` [budget]: budget: the route's share of the repo budget ($0.10) is reached
- `get_notes_sentiment` [budget]: budget: the repo's cost cap ($6.50) is reached
- `get_notes_related` [budget]: budget: the repo's cost cap ($6.50) is reached
- `get_note` [budget]: budget: the route's share of the repo budget ($0.22) is reached
- `update_note` [unsupported framework feature: dynamic dispatch]: cut off
- `get_user_tags` [budget]: budget: the route's share of the repo budget ($0.22) is reached
- `create_tag` [budget]: budget: the route's share of the repo budget ($0.22) is reached
- `add_tag_to_note` [budget]: budget: the route's share of the repo budget ($0.20) is reached
- `remove_tag_from_note` [budget]: budget: the route's share of the repo budget ($0.20) is reached
- `get_note_tags` [budget]: budget: the route's share of the repo budget ($0.14) is reached
- `get_user_keywords` [budget]: budget: the route's share of the repo budget ($0.22) is reached
- `create_keyword` [budget]: budget: the route's share of the repo budget ($0.22) is reached
- `add_keyword_to_note` [budget]: budget: the route's share of the repo budget ($0.18) is reached
- `remove_keyword_from_note` [budget]: budget: the route's share of the repo budget ($0.19) is reached
- `get_note_keywords` [budget]: budget: the route's share of the repo budget ($0.12) is reached
- `list_users` [budget]: budget: the route's share of the repo budget ($0.22) is reached

## Unclear profile decisions

- owner Note: non-admin routes create_notes, delete_note, get_note, get_notes, get_notes_auto_tag, get_notes_extend, get_notes_keywords, get_notes_related, get_notes_sentiment, get_notes_summarize, get_notes_translate, get_public_notes, update_note touch Note without comparing owner_id to the caller
- private NoteTagLink: no response model returns NoteTagLink; its private columns are not inferred
- private Tag: no response model returns Tag; its private columns are not inferred
- owner Tag: non-admin routes create_tag, delete_tag, get_user_tags touch Tag without comparing owner_id to the caller
- private NoteKeywordLink: no response model returns NoteKeywordLink; its private columns are not inferred
- private Keyword: no response model returns Keyword; its private columns are not inferred
- owner Keyword: non-admin routes create_keyword, delete_keyword, get_user_keywords touch Keyword without comparing owner_id to the caller
- auth User: the token's 'sub' claim names User.username, which is not declared unique (the app may enforce it in code only): the lookup is checked per request (NativeToken unique="runtime"): no row answers 401, several a loud 500 (R311, an ambiguous caller)
