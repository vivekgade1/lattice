# Lattice: exact role sets and stored role values (design)

> Status: approved by the user 2026-09-29, as a prerequisite for option 1's main
> experiment (`docs/superpowers/specs/2026-09-29-option1b-atomicity-pilot.md`, decision
> "GO"). Evidence (vision §3, "grow with evidence"): the held-out multi-vendor app
> (`bench/import_repos/multivendor`) has a role/permission table that today's `auth=`
> cannot express, so its routes stay islands and arm L would start unfaithful.

## Problem

`@endpoint(..., auth=R)` allows callers whose role is `R` **or the admin role**, plus
`Public` for anyone. Real permission tables need more:

| upstream permission | allowed roles | today |
|---|---|---|
| place an order | buyer only; the admin is refused | not expressible |
| manage own products | vendor only | not expressible |
| view own orders | buyer or vendor | not expressible |
| any signed-in user (a plain `get_current_user`) | every role | not expressible with three roles |

The role column also stores an enum's member names (`"BUYER"`), not role names.

## Design

All of it is additive, and existing programs keep their meaning (R13: stricter-only).

1. **`auth=Roles(A, B, ...)`** allows exactly these roles. The admin role is allowed only
   when it is listed; nothing is implied.
2. **`auth=Authenticated`** allows any authenticated, active caller, whatever their role.
3. **`auth=R`** is unchanged: `R` or the admin role.
4. **`RoleColumn(column, values={value: role})`** maps the column's value, as the
   profile reads it, to the role name before any comparison, including `is_admin(user)`
   and `owned()`/`accessible()`. For an enum column the ORM stores by member name
   (`one_of_stored`), that is the decoded value.
   A stored value outside the map is a role no endpoint allows (403). `values=None`, the
   default, keeps today's behaviour: the column holds role names.

### Checker (L801)

- `Roles(...)` takes one or more positional role names. Each must be in the profile's
  roles and must not be `Public`; none may repeat, and no keywords are allowed.
- `Authenticated` and `Roles` are reserved: a profile may not declare a role by either
  name (a `ValueError` at profile construction).
- With `NoRoles(role=R)`, `Roles(...)` may list only `R`, and `Authenticated` is allowed.
- Either form requires a `user: CurrentUser` parameter, as every non-`Public` endpoint does.
- Fix hints name the valid roles, and `Roles(...)` is suggested when an app needs the
  admin excluded.

### IR and targets

- `EndpointPlan` gains `roles: tuple[str, ...] | None`. `None` means the legacy rule;
  otherwise it is the exact allowed set, with `Authenticated` expanded to every
  non-`Public` role. It also gains `role_values: tuple[tuple[str, str], ...]`.
- The runtime (`lattice/api/runtime.py`) and the generated FastAPI app
  (`lattice/api/codegen.py`) implement the same check. They are kept equal by a new
  differential fixture (`tests/api/rolesets.py`) in `tests/api/test_codegen_differential.py`.
  T2's caller role (`lattice/t2/support.py: t2_role`) decodes stored values too.
- **The Go target** lives on another branch (`go-target`). When that branch merges, this
  construct needs either a Go implementation or a gate entry in
  `docs/lattice-language-rules.md`'s decision log. That is tracked there, not
  implemented here.

## The importer (separate change, same sub-project)

- **Dependency factories:** `Depends(f(args))`, where `f` returns an inner checker that
  depends on the current user and raises a 403 unless the caller's role is in a constant
  table, lifts to `auth=Roles(...)`. The table is evaluated statically; an unresolvable
  one stays "auth unclear".
- **Current user:** a plain current-user dependency lifts to `auth=Authenticated` when
  the profile has more than one non-admin role.
- **Role column:** `RoleColumn` with `values` is inferred from an enum role column.
- **Smaller fixes:** literal `Query(default=...)` defaults are accepted, and the
  generated seed respects unique foreign keys.
- **Out of scope:** `owner_via` inference for "product owned through vendor". It is
  recorded as a known limitation.

All importer fixes are tested on synthetic fixtures, never on the multi-vendor code.
