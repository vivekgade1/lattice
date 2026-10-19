As /4 (per-rpc `rpc`, `service`, `method`, `state` in `implemented | unimplemented | not_registered`, `streaming`, `auth` in `Public |
Member | <role>`, `handler` file:line, `codes`, `code_sites[{code, at, via}]`, `interceptor_codes`, `tables`, `writes`, `effects`; `login:
{service, method, username_field, password_field, token_field}`; `users`; `tables[{name, ddl, read_by, written_by, notes}]`; `flows`), with
these changes: (1) **every code is an UPPER_SNAKE `google.rpc.Code` name** (`OK`, `CANCELLED`, `UNKNOWN`, `INVALID_ARGUMENT`,
`DEADLINE_EXCEEDED`, `NOT_FOUND`, `ALREADY_EXISTS`, `PERMISSION_DENIED`, `RESOURCE_EXHAUSTED`, `FAILED_PRECONDITION`, `ABORTED`,
`OUT_OF_RANGE`, `UNIMPLEMENTED`, `INTERNAL`, `UNAVAILABLE`, `DATA_LOSS`, `UNAUTHENTICATED`); (2) `conventions.step_index_base` is required
(0 or 1); (3) a flow is `{name, steps: [{rpc, as, request, expect: {code, <dotted response path>: value, ...}}], links: [{from_step,
from_field, to_step, to_field}]}`, field paths dotted proto field names, numeric segments index lists, a final `#` a list length; (4)
`tables[*].ddl` is one of `go_literal` (a `CREATE TABLE` string in a `.go` file), `go_embed` (a SQL file embedded into the binary),
`harness` (only in `harness.py`), `migration_tool`, `other`; (5) `schema: "lattice.heldout.truth/5"`.
