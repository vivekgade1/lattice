# Lattice import of `bench.import_repos.justnotes.app`

- LLM loop: on, method M2, model claude-sonnet-5
- routes: 30; verified 2 (6.7%): Lattice R0-R2 2 (6.7%), carve-out R3 0 (0.0%)
- islands R4: 28 (93.3%); needing the LLM: 0 (0.0%)
- by rung: R0 1, R1 1, R2 0, R3 0, R4 28
- original lines by outcome: {'island': 833, 'lattice': 19}; AST nodes: {'island': 2204, 'lattice': 54}
- tier mix: T0 2, T1 0, T2 0, T3 0 (islands 28); R3 carve-outs: 0 procedure, 0 native
- cost: $2.7124 (notes $0.0000), 21 LLM rounds, 315357 input / 235833 output tokens, 2675.14 s
- budget: exhausted -- the repo's cost cap ($2.70) is reached (spent $2.7124, 21 rounds)

## Routes

| route | request | status | rung | by | tier | carve | tally eq/rule/dev/mis | rounds | cost | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| register | POST /auth/register | island | R4 |  | island |  |  | 7 | $0.7841 | cut off |
| login | POST /auth/login | island | R4 |  | island |  |  | 7 | $1.2002 | cut off |
| read_users_me | GET /auth/me | verified | R0 | lifter | T0 |  | 3/0/3/0 | 0 | $0.0000 |  |
| health | GET /health | verified | R1 | llm | T0 |  | 1/0/0/0 | 2 | $0.0671 | no lifter matches the statements at line 9 |
| get_notes | GET /notes | island | R4 |  | island |  |  | 5 | $0.6610 | budget: the repo's cost cap ($2.70) is reached |
| create_notes | POST /notes/create | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_public_notes | GET /notes/public | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_notes_summarize | GET /notes/{id}/summarize | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_notes_extend | GET /notes/{id}/extend | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_notes_translate | GET /notes/{id}/translate/{target_language} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_notes_auto_tag | GET /notes/{id}/auto-tag | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_notes_keywords | GET /notes/{id}/keywords | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_notes_sentiment | GET /notes/{id}/sentiment | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_notes_related | GET /notes/{id}/related | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_note | GET /notes/{id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| update_note | PUT /notes/{id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| delete_note | DELETE /notes/{id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_user_tags | GET /tags | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| create_tag | POST /tags | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| delete_tag | DELETE /tags/{tag_id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| add_tag_to_note | POST /notes/{note_id}/tags/{tag_id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| remove_tag_from_note | DELETE /notes/{note_id}/tags/{tag_id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_note_tags | GET /notes/{note_id}/tags | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_user_keywords | GET /keywords | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| create_keyword | POST /keywords | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| delete_keyword | DELETE /keywords/{keyword_id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| add_keyword_to_note | POST /notes/{note_id}/keywords/{keyword_id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| remove_keyword_from_note | DELETE /notes/{note_id}/keywords/{keyword_id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| get_note_keywords | GET /notes/{note_id}/keywords | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |
| list_users | GET /user/list | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($2.70) is reached |

## Behaviour changes (need human acceptance)

- `read_users_me` invalid-token-401 (1 cases): The original answers an invalid bearer token with 401 'Could not validate credentials'; Lattice's auth answers every missing or invalid credential with 401.
- `read_users_me` unknown-user-token (1 cases): A valid token naming a user that does not exist: the original answers 401 'User not found'; Lattice's token auth answers 401 like any token naming no caller.
- `read_users_me` claimless-token (1 cases): A valid token without the caller's claim: the original answers 401 'Could not validate credentials'; Lattice's token auth answers 401 like any token naming no caller.

## Islands

- `register` [missing construct]: cut off
- `login` [missing construct]: cut off
- `get_notes` [budget]: budget: the repo's cost cap ($2.70) is reached
- `create_notes` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_public_notes` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_notes_summarize` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_notes_extend` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_notes_translate` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_notes_auto_tag` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_notes_keywords` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_notes_sentiment` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_notes_related` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_note` [budget]: budget: the repo's cost cap ($2.70) is reached
- `update_note` [budget]: budget: the repo's cost cap ($2.70) is reached
- `delete_note` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_user_tags` [budget]: budget: the repo's cost cap ($2.70) is reached
- `create_tag` [budget]: budget: the repo's cost cap ($2.70) is reached
- `delete_tag` [budget]: budget: the repo's cost cap ($2.70) is reached
- `add_tag_to_note` [budget]: budget: the repo's cost cap ($2.70) is reached
- `remove_tag_from_note` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_note_tags` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_user_keywords` [budget]: budget: the repo's cost cap ($2.70) is reached
- `create_keyword` [budget]: budget: the repo's cost cap ($2.70) is reached
- `delete_keyword` [budget]: budget: the repo's cost cap ($2.70) is reached
- `add_keyword_to_note` [budget]: budget: the repo's cost cap ($2.70) is reached
- `remove_keyword_from_note` [budget]: budget: the repo's cost cap ($2.70) is reached
- `get_note_keywords` [budget]: budget: the repo's cost cap ($2.70) is reached
- `list_users` [budget]: budget: the repo's cost cap ($2.70) is reached

## Unclear profile decisions

- owner Note: non-admin routes create_notes, delete_note, get_note, get_notes, get_notes_auto_tag, get_notes_extend, get_notes_keywords, get_notes_related, get_notes_sentiment, get_notes_summarize, get_notes_translate, get_public_notes, update_note touch Note without comparing owner_id to the caller
- private NoteTagLink: no response model returns NoteTagLink; its private columns are not inferred
- private Tag: no response model returns Tag; its private columns are not inferred
- owner Tag: non-admin routes create_tag, delete_tag, get_user_tags touch Tag without comparing owner_id to the caller
- private NoteKeywordLink: no response model returns NoteKeywordLink; its private columns are not inferred
- private Keyword: no response model returns Keyword; its private columns are not inferred
- owner Keyword: non-admin routes create_keyword, delete_keyword, get_user_keywords touch Keyword without comparing owner_id to the caller
- auth User: the token's 'sub' claim names User.username, which is not declared unique (the app may enforce it in code only): the lookup is checked per request (NativeToken unique="runtime"): no row answers 401, several a loud 500 (R311, an ambiguous caller)
