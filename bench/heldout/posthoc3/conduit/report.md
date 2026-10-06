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
- private Tag: no response model returns Tag; its private columns are not inferred
- private Comment: no response model returns Comment; its private columns are not inferred
- private User_favorites: no response model returns User_favorites; its private columns are not inferred
- owner User_favorites: no route filters, checks or sets User_favorites.user_id from the caller
- private Topic_tags: no response model returns Topic_tags; its private columns are not inferred
- auth User: the app's own auth (django, bearer) is not lifted: the profile's NativeToken names caller_key, which refuses to run (lattice.importer.app_auth)
- route_auth user_view_set_update: permission_classes: IsAuthenticated, IsAdminOrSelf; authentication_classes: JWTAuthentication, SessionAuthentication (a permission class the front end cannot read)

## Rule layer (layer R on the original)

- tables verified: 3 of 6; routes verified: 18 of 26; items: verified 46, refuted 0, unexercised 17, unclear 2, verified (corrected) 5

| item | verdict | reason |
|---|---|---|
| T1 Comment | unexercised | no probe confirmed or contradicted it: another user's row answered 403, nothing written; not decisive: the caller got past the route's check by no request: its role's refusal is not ruled out |
| T1 User_favorites | unclear | the owner is unclear: no route filters, checks or sets User_favorites.user_id from the caller |
| T1 Topic_tags | unexercised | no probe reached it |
| T5 User (_id) | unexercised | no route creates User rows |
| T5 Topic (_id) | unexercised | no route creates Topic rows |
| T5 Tag (_id) | unexercised | no route creates Tag rows |
| T5 Comment (_id) | unexercised | no route creates Comment rows |
| T5 User_favorites (id) | unexercised | no route creates User_favorites rows |
| T5 Topic_tags (id) | unexercised | no route creates Topic_tags rows |
| R1 POST /api/topics/ | unexercised | no answer decides Admin, Customer -- Admin: a crash: before or past the auth check, not told apart; Customer: a crash: before or past the auth check, not told apart |
| R3 POST /api/topics/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 PUT /api/topic/{pk}/ | unexercised | no answer decides Admin, Customer -- Admin: a crash: before or past the auth check, not told apart; a 2xx error envelope (an error envelope: {'code': 404, 'msg': 'Topic not found.'}); Customer: a cras |
| R2 PUT /api/topic/{pk}/ | unexercised | no probe confirmed or contradicted it: another user's row answered 500: neither a reach nor a refusal; another user's row answered 403, nothing written; not decisive: the caller got past the route's c |
| R3 PUT /api/topic/{pk}/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 GET /api/profile/{username}/ | unexercised | no answer decides Admin, Customer -- Admin: a crash: before or past the auth check, not told apart; Customer: a crash: before or past the auth check, not told apart |
| R3 GET /api/profile/{username}/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 GET /api/profile/{username}/favorites/ | unexercised | no answer decides Admin, Customer -- Admin: a crash: before or past the auth check, not told apart; Customer: a crash: before or past the auth check, not told apart |
| R3 GET /api/profile/{username}/favorites/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 POST /api/topic/{pk}/comment/ | unexercised | no answer decides Admin, Customer -- Admin: a 2xx error envelope (an error envelope: {'code': 400, 'error': {'content': ['This field is required.']}, 'msg': 'Topic c); Customer: a 2xx error envelope ( |
| R1 DELETE /api/topic/{_id}/comment/{pk}/ | unexercised | no answer decides Admin, Customer -- Admin: a crash: before or past the auth check, not told apart; a 2xx error envelope (an error envelope: {'code': 404, 'msg': 'Comment not found.'}); Customer: a cr |
| R2 DELETE /api/topic/{_id}/comment/{pk}/ | unexercised | no probe confirmed or contradicted it: another user's row answered 500: neither a reach nor a refusal; another user's row answered 403, nothing written; not decisive: the caller got past the route's c |
| R3 DELETE /api/topic/{_id}/comment/{pk}/ 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 POST /api/users/ | unexercised | no answer decides Admin, Customer -- Admin: a 2xx error envelope (an error envelope: {'code': 400, 'error': {'confirm_password': ['This field is required.'], 'email'); Customer: a 2xx error envelope ( |
| R1 PUT /api/user/{username}/ | unclear | the route's auth is unknown |
