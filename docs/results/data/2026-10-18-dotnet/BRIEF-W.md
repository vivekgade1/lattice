You write 16 mutants of one C# service. You are given the service (a module directory) and its `truth.json` only. You never run Lattice, never
read the repository outside this directory, and never read the checker's results. Each mutant is a patch against the module that builds with
0 errors (`dotnet build -c Release -p:RestoreLockedMode=true`) and changes behaviour in exactly one way.

Instance rule: in `truth.json` order, take the first and the last route that fits the operator. When only one route fits an operator, use it
once and give the next operator three instances. Record every substitution.

| ids | operator (write this word exactly in the operator cell) | what to change |
|---|---|---|
| HM1-2 | owner_read | drop the owner condition of an owner-scoped read |
| HM3-4 | owner_write | drop the owner condition of an owner-scoped write or state change |
| HM5-6 | expose_column | expose a never-returned column on a route that answers its row |
| HM7-8 | drop_login | remove the login requirement from a Member route |
| HM9-10 | status_403 | answer 403 where truth says 404 `not_found` |
| HM11 | drop_parent | drop the parent-ownership check of an `owner_via` table |
| HM12-13 | drop_guard | drop a state guard (`conflict`) of a transition |
| HM14 | loosen_bound | remove an upper bound from a validated field |
| HM15 | owner_body | take the owner from the request body on create |
| HM16 | widen_list | widen a list route's owner condition to every row |

Deliver `MUTANTS.md`: one line per mutant, exactly
`HMn | <operator> | METHOD /path | file:line | substitutions`, and `patches/HMn.patch` (a unified diff that applies with `patch -p1` at the
module root). A line without the operator cell is read by its id. If an operator has no fit in this service, write `HMn | <operator> | NONE |`
and the reason; do not substitute another operator.
