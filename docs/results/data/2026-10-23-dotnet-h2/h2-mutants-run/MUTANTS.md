HM1 | owner_read | GET /hives/{id} | Endpoints/HiveEndpoints.cs:49 | none (first by-id owner-scoped read; GET /hives and GET /lots lists kept for widen_list)
HM2 | owner_read | GET /lots/{id} | Endpoints/LotEndpoints.cs:54 | none (last by-id owner-scoped read)
HM3 | owner_write | DELETE /hives/{id} | Endpoints/HiveEndpoints.cs:57 | none (first owner-scoped write)
HM4 | owner_write | POST /lots/{id}/advance | Endpoints/LotEndpoints.cs:67 | none (last owner-scoped write; owner dropped from both the lookup at :67 and the atomic update at :79, else the update alone would still refuse)
HM5 | expose_column | POST /hives | Endpoints/HiveEndpoints.cs:43 | yard_location exposed; fitting routes taken as those answering a single hive row (POST /hives, GET /hives/{id}), so GET /hives list is not used
HM6 | expose_column | GET /hives/{id} | Endpoints/HiveEndpoints.cs:50 | yard_location exposed; last single-row route (see HM5)
HM7 | drop_login | GET /hives | Endpoints/HiveEndpoints.cs:20 | none (first Member route; RequireSignIn removed, so Caller.Of fails with 500 when anonymous)
HM8 | drop_login | POST /lots/{id}/advance | Endpoints/LotEndpoints.cs:58 | none (last Member route; RequireSignIn removed at :86)
HM9 | status_403 | GET /hives/{id} | Endpoints/HiveEndpoints.cs:50 | none (first 404 route)
HM10 | status_403 | POST /lots/{id}/advance | Endpoints/LotEndpoints.cs:68 | none (last 404 route)
HM11 | drop_parent | GET /hives/{id}/inspections | Endpoints/HiveEndpoints.cs:67 | none (first of two routes via the hive; POST would also fit)
HM12 | drop_guard | POST /lots/{id}/advance | Endpoints/LotEndpoints.cs:71 | CanMove transition guard dropped
HM13 | drop_guard | POST /lots/{id}/advance | Endpoints/LotEndpoints.cs:74 | substitution: only one route has a transition guard, so the second instance is a second guard on the same route (too-wet-to-bottle); the last guard (state in WHERE, :82) is invisible without a race, so not used
HM14 | loosen_bound | POST /hives | Endpoints/HiveEndpoints.cs:32 | queen_year upper bound (current UTC year) removed; first bound with an upper limit
HM15 | owner_body | POST /hives | Endpoints/HiveEndpoints.cs:40 | optional owner_id added to HiveBody in Http/Dtos.cs:5 (a second file in the diff)
HM16 | widen_list | GET /hives | Endpoints/HiveEndpoints.cs:23 | none (first list route)
