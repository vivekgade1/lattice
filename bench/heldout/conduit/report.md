# Lattice import of `bench.import_repos.conduit`

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

- column_type User.gender: SmallIntegerField allows only -1, 0, 1; kept as Int: language gap (enum constraint) -- Lattice's OneOf constraint is on Str columns and parameters only
- private User: no response model returns User; its private columns are not inferred
- private Topic: no response model returns Topic; its private columns are not inferred
- owner Topic: no route filters, checks or sets Topic.user_id from the caller
- private Tag: no response model returns Tag; its private columns are not inferred
- private Comment: no response model returns Comment; its private columns are not inferred
- owner Comment: no route filters, checks or sets Comment.user_id from the caller
- private User_favorites: no response model returns User_favorites; its private columns are not inferred
- owner User_favorites: no route filters, checks or sets User_favorites.user_id from the caller
- private Topic_tags: no response model returns Topic_tags; its private columns are not inferred
- auth User: the app's own auth (django, bearer) is not lifted: the profile's NativeToken names caller_key, which refuses to run (lattice.importer.app_auth)

## Rule layer (layer R on the original)

- tables verified: 1 of 1; routes verified: 24 of 26; items: verified 38, refuted 2, unexercised 11, unclear 0, verified (corrected) 13
- outside the API (no route reads or writes them; listed, not in the table count): Topic (no route reads or writes Topic (api_topic)); Tag (no route reads or writes Tag (api_tag)); Comment (no route reads or writes Comment (api_comment)); User_favorites (no route reads or writes User_favorites (api_user_favorites)); Topic_tags (no route reads or writes Topic_tags (api_topic_tags))

| item | verdict | reason |
|---|---|---|
| T1 Topic | unexercised | outside API: no route reads or writes Topic (api_topic) |
| T1 Tag | unexercised | outside API: no route reads or writes Tag (api_tag) |
| T1 Comment | unexercised | outside API: no route reads or writes Comment (api_comment) |
| T1 User_favorites | unexercised | outside API: no route reads or writes User_favorites (api_user_favorites) |
| T1 Topic_tags | unexercised | outside API: no route reads or writes Topic_tags (api_topic_tags) |
| T5 User (_id) | unexercised | no route creates User rows |
| T5 Topic (_id) | unexercised | outside API: no route reads or writes Topic (api_topic) |
| T5 Tag (_id) | unexercised | outside API: no route reads or writes Tag (api_tag) |
| T5 Comment (_id) | unexercised | outside API: no route reads or writes Comment (api_comment) |
| T5 User_favorites (id) | unexercised | outside API: no route reads or writes User_favorites (api_user_favorites) |
| T5 Topic_tags (id) | unexercised | outside API: no route reads or writes Topic_tags (api_topic_tags) |
| R3 POST /api/topics/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 DELETE /api/topic/{pk}/ | verified (corrected) | corrected (auth Customer -> Admin); 5 callers as predicted |
| R1 PUT /api/topic/{pk}/ | verified (corrected) | corrected (auth Customer -> Admin); 5 callers as predicted |
| R3 PUT /api/topic/{pk}/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 GET /api/my-topics/ | verified (corrected) | corrected (auth Public -> Authenticated); 5 callers as predicted |
| R1 GET /api/my-favorites/ | verified (corrected) | corrected (auth Public -> Authenticated); 5 callers as predicted |
| R1 GET /api/profile/{username}/ | verified (corrected) | corrected (auth Public -> Authenticated); 5 callers as predicted |
| R3 GET /api/profile/{username}/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 GET /api/profile/{username}/favorites/ | verified (corrected) | corrected (auth Public -> Authenticated); 5 callers as predicted |
| R3 GET /api/profile/{username}/favorites/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 DELETE /api/topic/{_id}/comment/{pk}/ | verified (corrected) | corrected (auth Customer -> Admin); 5 callers as predicted |
| R3 DELETE /api/topic/{_id}/comment/{pk}/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 DELETE /api/user/{username}/ | verified (corrected) | corrected (auth Customer -> Admin); 5 callers as predicted |
| R1 POST /api/token/ | refuted | callers answered against the auth: anonymous refused, Admin refused, Customer refused |
| R1 POST /api/token/refresh/ | refuted | callers answered against the auth: anonymous refused, Admin refused, Customer refused |
