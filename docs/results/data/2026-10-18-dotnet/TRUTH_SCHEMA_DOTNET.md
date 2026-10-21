# truth.json schema (referred to as section 8 of HARNESS_API_DOTNET.md)

Write `truth.json` in exactly this schema, from your finished code, with a `file:line` for every fact (paths relative to the project directory). Extra keys are allowed.

`routes[{method, template (full, as registered), auth: Public|Member|Admin, style: controller|minimal, file, line}]`;
`tables[{entity, table, columns[{name (SQL), wire (JSON name or null if never serialized), private, nullable, unique, pk, max_length,
fk}]}]`; `owners {entity: column}`; `owner_via {entity: [parent_entity, fk_column]}`; `machines {"Entity.column": {initial, edges[[from,
to]], storage: string|int}}`; `refusals[{route, status, detail, cause: auth|not_found|not_owned|invalid|conflict|other, file, line}]`;
`bounds[{route, field (wire), required, ge, le, min_length, max_length, enforced_by, file, line}]`; `bind_status`; `json_naming`;
`login {path, fields (wire), token_field}`; `seed_users[{name, role}]`; `not_returned[{entity, column}]`.

**Matching rules (fixed now, so no scoring choice is made after a number is seen):** a route matches when the method is equal and the
templates are equal after lowercasing literal segments, replacing every `{...}` by `{}` and dropping a trailing `/`; a table by SQL
name (case-insensitive on MySQL, exact on SQLite); a column fact by (table, column, fact); an owner by (entity, column); a refusal by
(route, status); a bound by (route, field, bound kind, value).
