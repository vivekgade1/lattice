# Lattice import of `bench.import_repos.drf_blog`

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

- private Category: no response model returns Category; its private columns are not inferred
- private Comment: no response model returns Comment; its private columns are not inferred
- private Blog_category: no response model returns Blog_category; its private columns are not inferred
- private Blog_likes: no response model returns Blog_likes; its private columns are not inferred
- owner Blog_likes: no route filters, checks or sets Blog_likes.user_id from the caller
- auth User: the app's own auth (django, bearer) is not lifted: the profile's NativeToken names caller_key, which refuses to run (lattice.importer.app_auth)
- route_auth blog_create_post: permission_classes: IsSuperUserOrAuthor; authentication_classes: SessionAuthentication, JWTAuthentication (a permission class the front end cannot read)

## Rule layer (layer R on the original)

- tables verified: 1 of 6; routes verified: 17 of 30; items: verified 69, refuted 16, unexercised 44, unclear 1, verified (corrected) 13

| item | verdict | reason |
|---|---|---|
| T1 Category | unexercised | no probe reached it |
| T1 Blog_category | unexercised | no probe reached it |
| T1 Blog_likes | unclear | the owner is unclear: no route filters, checks or sets Blog_likes.user_id from the caller |
| T3 Blog.summary max_len 400 | refuted | the boundary value answered 404 |
| T3 Blog.title max_len 200 | refuted | the boundary value answered 404 |
| T3 Blog.visits ge 0 | refuted | the boundary value answered 404 |
| T3 Blog.visits le 9223372036854775807 | refuted | the boundary value answered 404 |
| T3 User.first_name max_len 100 | refuted | the boundary value answered 400 |
| T3 User.last_name max_len 100 | refuted | the boundary value answered 400 |
| T3 User.phone max_len 12 | refuted | the boundary value answered 400 |
| T5 Blog (id) | unexercised | no route can repeat the key: no create route of Blog takes (id) or stamps it from the caller |
| T5 Category (id) | unexercised | no route creates Category rows |
| T5 User (id) | unexercised | no route creates User rows |
| T5 Comment (id) | unexercised | no route creates Comment rows |
| T5 Blog_category (id) | unexercised | no route creates Blog_category rows |
| T5 Blog_likes (id) | unexercised | no route creates Blog_likes rows |
| R1 GET /account/api/ | refuted | callers of one role answered differently (Customer: alice refused, bob admitted): the profile's roles miss a distinction the route makes |
| R3 GET /account/api/ 400 | verified (corrected) | corrected (400 None -> 404 'Invalid page.'); answered 404 'Invalid page.'; stated 404 'Invalid page.' |
| R3 PUT /account/api/profile/ 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PATCH /account/api/profile/ 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/login/ 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/login/ 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/register/ 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/register/ 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/verify/ 406 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/verify/ 408 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/verify/ 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/verify-two-step-password/ 406 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/verify-two-step-password/ 408 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/verify-two-step-password/ 400 | unexercised | unreachable-by-probe: no example triggers it |
| R1 POST /account/api/change-two-step-password/ | refuted | callers of one role answered differently (Customer: alice refused, bob admitted): the profile's roles miss a distinction the route makes |
| R3 POST /account/api/change-two-step-password/ 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/change-two-step-password/ 404 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/change-two-step-password/ 406 | unexercised | unreachable-by-probe: no example triggers it |
| R1 POST /account/api/create-two-step-password/ | refuted | callers of one role answered differently (Customer: alice admitted, bob refused): the profile's roles miss a distinction the route makes |
| R3 POST /account/api/create-two-step-password/ 401 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /account/api/create-two-step-password/ 404 | unexercised | unreachable-by-probe: no example triggers it |
| R3 DELETE /account/api/delete-account/ 406 | unexercised | unreachable-by-probe: no example triggers it |
| R1 GET /account/api/users/{pk}/ | verified (corrected) | corrected (auth Admin -> Authenticated); 4 callers as predicted |
| R3 GET /account/api/users/{pk}/ 404 No User matches the given query. | unexercised | unreachable-by-probe: no example triggers it |
| R1 PUT /account/api/users/{pk}/ | verified (corrected) | corrected (auth Admin -> Authenticated); 4 callers as predicted |
| R3 PUT /account/api/users/{pk}/ 404 No User matches the given query. | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /account/api/users/{pk}/ 400 | unexercised | no probe reached it |
| R1 PATCH /account/api/users/{pk}/ | verified (corrected) | corrected (auth Admin -> Authenticated); 4 callers as predicted |
| R3 PATCH /account/api/users/{pk}/ 404 No User matches the given query. | unexercised | unreachable-by-probe: no example triggers it |
| R3 PATCH /account/api/users/{pk}/ 400 | unexercised | no probe reached it |
| R1 DELETE /account/api/users/{pk}/ | verified (corrected) | corrected (auth Admin -> Authenticated); 4 callers as predicted |
| R3 DELETE /account/api/users/{pk}/ 404 No User matches the given query. | unexercised | unreachable-by-probe: no example triggers it |
| R2 GET /blog/api/ | unexercised | no probe confirmed or contradicted it: no row of the table in the answer; no row of the table in the answer |
| R3 GET /blog/api/ 400 | verified (corrected) | corrected (400 None -> 404 'Invalid page.'); answered 404 'Invalid page.'; stated 404 'Invalid page.' |
| R1 POST /blog/api/create/ | refuted | callers of one role answered differently (Customer: alice refused, bob admitted): the profile's roles miss a distinction the route makes |
| R3 POST /blog/api/create/ 400 | unexercised | unreachable-by-probe: a multipart file upload (image); probes send JSON -- unreachable-by-probe: no example triggers it |
| R3 GET /blog/api/category/list/ 400 | verified (corrected) | corrected (400 None -> 404 'Invalid page.'); answered 404 'Invalid page.'; stated 404 'Invalid page.' |
| R2 GET /blog/api/category/{slug}/ | refuted | another user's row answered 404 |
| R3 GET /blog/api/category/{slug}/ 400 | verified (corrected) | corrected (400 None -> 404 None); answered 404 'No Category matches the given query.'; stated 404 None |
| R2 GET /blog/api/{slug}/ | refuted | another user's row answered 404 |
| R1 PUT /blog/api/{slug}/ | verified (corrected) | corrected (auth Customer -> Public); 4 callers as predicted |
| R2 PUT /blog/api/{slug}/ | refuted | another user's row answered 404 |
| R3 PUT /blog/api/{slug}/ 400 | unexercised | unreachable-by-probe: a multipart file upload (image); probes send JSON -- unreachable-by-probe: no example triggers it |
| R3 PUT /blog/api/{slug}/ 400 #2 | verified (corrected) | corrected (400 None -> 404 None); answered 404 None; stated 404 None |
| R1 PATCH /blog/api/{slug}/ | verified (corrected) | corrected (auth Customer -> Public); 4 callers as predicted |
| R2 PATCH /blog/api/{slug}/ | refuted | another user's row answered 404 |
| R3 PATCH /blog/api/{slug}/ 400 | unexercised | unreachable-by-probe: a multipart file upload (image); probes send JSON -- unreachable-by-probe: no example triggers it |
| R3 PATCH /blog/api/{slug}/ 400 #2 | verified (corrected) | corrected (400 None -> 404 None); answered 404 None; stated 404 None |
| R1 DELETE /blog/api/{slug}/ | verified (corrected) | corrected (auth Customer -> Public); 4 callers as predicted |
| R2 DELETE /blog/api/{slug}/ | refuted | another user's row answered 404 |
| R3 GET /blog/api/like/{pk}/ 404 | unexercised | unreachable-by-probe: the route addresses no row by id |
| R3 GET /comment/api/{pk}/ 404 | unexercised | unreachable-by-probe: the route addresses no row by id |
| R2 POST /comment/api/create/ | unexercised | no probe reached it |
| R3 POST /comment/api/create/ 404 | unexercised | unreachable-by-probe: no example triggers it |
| R3 POST /comment/api/create/ 400 | unexercised | unreachable-by-probe: no example triggers it |
| R3 PUT /comment/api/update-delete/{pk}/ 400 | unexercised | unreachable-by-probe: no example triggers it |
| R1 POST /api/token/refresh/ | unexercised | no role caller's refusal could be told apart |
| R1 POST /api/token/verify/ | unexercised | no role caller's refusal could be told apart |
