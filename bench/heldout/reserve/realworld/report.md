# Lattice import of `bench.import_repos.realworld`

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

- private favoritor_assoc: no response model returns favoritor_assoc; its private columns are not inferred
- private tag_assoc: no response model returns tag_assoc; its private columns are not inferred
- private Tags: no response model returns Tags; its private columns are not inferred
- private Comment: no response model returns Comment; its private columns are not inferred
- private Article: no response model returns Article; its private columns are not inferred
- private followers_assoc: no response model returns followers_assoc; its private columns are not inferred
- private UserProfile: no response model returns UserProfile; its private columns are not inferred
- owner UserProfile: no route filters, checks or sets UserProfile.user_id from the caller
- column_type User.password: column type LargeBinary(128) has no Lattice scalar; column left out
- private User: no response model returns User; its private columns are not inferred
- auth User: the app's own auth (flask, bearer) is not lifted: the profile's NativeToken names caller_key, which refuses to run (lattice.importer.app_auth)

## Rule layer (layer R on the original)

- tables verified: 1 of 5; routes verified: 0 of 19; items: verified 6, refuted 12, unexercised 30, unclear 1, verified (corrected) 2
- outside the API (no route reads or writes them; listed, not in the table count): favoritor_assoc (no route reads or writes favoritor_assoc (favoritor_assoc)); tag_assoc (no route reads or writes tag_assoc (tag_assoc)); followers_assoc (no route reads or writes followers_assoc (followers_assoc))

| item | verdict | reason |
|---|---|---|
| T1 favoritor_assoc | unexercised | outside API: no route reads or writes favoritor_assoc (favoritor_assoc) |
| T1 tag_assoc | unexercised | outside API: no route reads or writes tag_assoc (tag_assoc) |
| T1 Tags | unexercised | no probe reached it |
| T1 Comment | unexercised | no probe reached it |
| T1 followers_assoc | unexercised | outside API: no route reads or writes followers_assoc (followers_assoc) |
| T1 UserProfile | unclear | the owner is unclear: no route filters, checks or sets UserProfile.user_id from the caller |
| T1 User | unexercised | no probe reached it |
| T5 Tags (id) | unexercised | no route creates Tags rows |
| T5 Comment (id) | unexercised | no route creates Comment rows |
| T5 Article (id) | unexercised | no route creates Article rows |
| T5 UserProfile (id) | unexercised | no route creates UserProfile rows |
| T5 User (id) | unexercised | no route creates User rows |
| R1 POST /api/users | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted |
| R3 POST /api/users 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 POST /api/users/login | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted |
| R3 POST /api/users/login 5xx | verified (corrected) | corrected (states the app's 500); answered 500 None; stated 500 None |
| R1 GET /api/user | refuted | callers answered against the auth: anonymous refused |
| R3 GET /api/user 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 PUT /api/user | refuted | callers answered against the auth: anonymous refused |
| R3 PUT /api/user 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 GET /api/profiles/{username} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted |
| R1 POST /api/profiles/{username}/follow | refuted | callers answered against the auth: anonymous refused |
| R3 POST /api/profiles/{username}/follow 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 DELETE /api/profiles/{username}/follow | refuted | callers answered against the auth: anonymous refused |
| R3 DELETE /api/profiles/{username}/follow 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 GET /api/articles | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 POST /api/articles | refuted | callers answered against the auth: anonymous refused |
| R3 POST /api/articles 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 PUT /api/articles/{slug} | refuted | callers answered against the auth: anonymous refused |
| R3 PUT /api/articles/{slug} 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 DELETE /api/articles/{slug} | refuted | callers answered against the auth: anonymous refused |
| R3 DELETE /api/articles/{slug} 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 GET /api/articles/{slug} | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 POST /api/articles/{slug}/favorite | refuted | callers answered against the auth: anonymous refused |
| R3 POST /api/articles/{slug}/favorite 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 DELETE /api/articles/{slug}/favorite | refuted | callers answered against the auth: anonymous refused |
| R3 DELETE /api/articles/{slug}/favorite 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 GET /api/articles/feed | refuted | callers answered against the auth: anonymous refused |
| R3 GET /api/articles/feed 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 GET /api/tags | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 GET /api/articles/{slug}/comments | unexercised | no callers: no user of any role was logged in, so no probe shows who is admitted (2 non-role callers as predicted) |
| R1 POST /api/articles/{slug}/comments | refuted | callers answered against the auth: anonymous refused |
| R3 POST /api/articles/{slug}/comments 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
| R1 DELETE /api/articles/{slug}/comments/{cid} | refuted | callers answered against the auth: anonymous refused |
| R3 DELETE /api/articles/{slug}/comments/{cid} 5xx | unexercised | no probe got past the auth check to the handler (answers [401]) |
