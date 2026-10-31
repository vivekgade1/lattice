# gRPC status codes on SG and HO5: diagnosis (node GA2, 2026-10-30)

Development data only. SG (Streak as grpc-go over MySQL, `~/Documents/g5-sg-author/app`) and HO5
(`harborlight`, `~/Documents/g5-ho5-author/app`) were scored once each in gRPC round 5
(`2026-10-18-grpc-round5-scored-results.md`). Both are now dev data: nothing below backs a claim,
and any number after a fix is a dev measurement, not a held-out result.

## Method

* Both apps copied read-only into the session scratchpad (`cp -R`); nothing under `~/Documents/g5-*`
  was written. The extractor was run on the copies from the GA2 worktree at `b464b26` (integration
  tip, after G9) through `GoGrpcFrontEnd().extract_json(root.parent, root.name)`, the call
  `load_app5` makes in `dogfood/grpc_probes5.py`.
* The CODES row was recomputed with the frozen scorers exactly as `grpc_probes4.rpc_rows4` does it:
  truth through `grpc_truth5.load_truth(..., strict=False)`, then `grpc_scorer4.score_codes`
  (implemented rpcs only, `OK` dropped, `unimplemented_stub` codes dropped).
* Reproduction of the round-5 numbers on the starting tree:

| app | truth pairs | extracted | matched | recall | precision | `UNKNOWN` codes on rpcs |
|---|---|---|---|---|---|---|
| SG | 132 | 25 | 0 | 0.000 | 0.000 | 91 (every extracted code) |
| HO5 | 83 | 70 | 56 | 0.675 | 0.800 | 0 |

Both equal the scored round-5 rows (SG-CODES 0 of 132, all `UNKNOWN`; HO5-CODES 56 of 83, 0.80).

Where the SG truth pairs come from (from `truth.json` `code_sites`/`interceptor_codes`): 38 are
handler codes (`fail("...")` sites), 68 are codes the shared unary interceptor answers
(`FAILED_PRECONDITION` version gate, `UNAUTHENTICATED`, `RESOURCE_EXHAUSTED`), 26 are the
interceptor's `INTERNAL` default for any non-application error.

## SG: why every code is `UNKNOWN`

The SG idiom (`internal/app/errors.go`): a refusal is `fail("<stable code>")`, which builds an
`*AppError` whose `GRPCStatus()` returns `status.New(e.Status, ...)` (through `WithDetails`), and
`Status` comes from a string-keyed package map:

```go
func fail(code string) *AppError {
	st, ok := codeStatus[code]
	if !ok {
		st = codes.Unknown
	}
	return &AppError{Code: code, Status: st}
}
func failField(code, field string) *AppError { e := fail(code); e.Field = field; return e }
func invalid(field string) *AppError { return failField("validation_failed", field) }
```

Every one of the 45 `fail("...")` and 3 `failField`/`invalid` call sites in handlers and helpers passes
a string literal. The extractor reaches the right place (E3a, `statusOf` -> `siteField`) and then:

**SG-1 (defect, a wrong literal, 91 `UNKNOWN` codes).** `siteField` reads the `Status:` field of the
literal `fail` returns, which is the local `st`, and asks `codeOf` for its value. `codeOf` accepts a
local when `Env.valuesOf` holds exactly one value. `Env` (types.go, `EnvOf`) records a value only for
an assignment with as many right-hand sides as left-hand sides, so the comma-ok lookup
`st, ok := codeStatus[code]` is not recorded and the fallback `st = codes.Unknown` is the only value
seen. The extractor therefore reports `UNKNOWN` (`kind: status_error`) at every site as if it were
certain. Evidence: `svc_account.go:25 fail("invalid_token")` -> `UNKNOWN status_error` (truth
`UNAUTHENTICATED`), and the same at all 33 distinct `fail` sites in the extract (70 of the 91
`UNKNOWN` codes on rpcs; the other 21 are `plain_error` placeholders for untranslated plain errors,
see SG-3). The rule is
unsound for any local that also has a multi-value assignment.

**SG-2 (gap, 56 `grpc_code_not_literal` items over the rpcs).** `invalid(...)` and `failField(...)` are constructors
whose return is not a composite literal: `invalid` returns another constructor's call, and
`failField` returns a local initialised from `fail(code)`. `siteField` reads only a constructor
whose first return is `&T{...}` and binds only one level of parameters, so these sites are
reported "the code of `invalid(\"timezone\")` is chosen at run time". The map lookup itself
(`codeStatus[code]`, key = a parameter bound to a literal at the call site) is also not read
inside `siteField` (the existing map reader `codesFrom` is used only for `status.Error(m[k], ...)`).

**SG-3 (gap, every interceptor code and `INTERNAL`, 94 of 132 pairs).** The interceptor
(`internal/app/interceptor.go`) answers every refusal through the translator
`toStatusError(method, err)`:

```go
var ae *AppError
if errors.As(err, &ae) { return ae.GRPCStatus().Err() }
if _, ok := status.FromError(err); ok { return err }
return status.Error(codes.Internal, "internal")
```

`errOfFunc` reads `ae.GRPCStatus().Err()` as an untraced call (`Err` of something that is not
`status.New`), and `return err` as "the error `err` is not assigned in the function": an error
**parameter** is never bound to what the caller passes (only `codes.Code` parameters are, E3b).
So the interceptor's own refusals (`fail("upgrade_required")`, `fail("not_authenticated")`,
`fail("rate_limited")`, all passed into `toStatusError`) are lost. Evidence: the extract lists the
interceptor as `auth_unknown`, reason `internal/app/errors.go:114 the error of
ae.GRPCStatus().Err() is not traced`, and every Member rpc carries an `interceptor_not_literal`
unread. Because E3d adds interceptor codes only from an `auth_literal` interceptor, none of the 68
interceptor pairs and none of the 26 `INTERNAL` pairs can be read while this holds. Whether the
interceptor then classifies as a literal gate is an auth question (out of scope, below); the code
reading is not.

Not causes (checked): `codes` is imported under its own name (no alias); `status.FromError` appears
only in the translator; there is no `GRPCStatus` on a value type; the generated code is standard.

## HO5: the 27 missed and 14 extra pairs

**HO5-1 (gap, BillingService, 19 missed pairs; also ForecastService's state).** Billing is attached
through a table (`internal/server/bindings.go`):
`func descriptorBindings(db) []binding { return []binding{{desc: &pb.BillingService_ServiceDesc, impl: billing.NewService(db)}} }`
and `for _, bind := range descriptorBindings(db) { gs.RegisterService(bind.desc, bind.impl) }`
(`server.go:85-86`). The registration reader resolves `RegisterService`'s descriptor argument from
`&pkg.X_ServiceDesc`, a local copy or a function returning one (E1), but not from a field of a range
value over a slice literal a module function returns. The registration is "not resolved to a
service", so every rpc of every service not registered elsewhere becomes `unread`
(`registration_reachability_unknown`): the 6 Billing rpcs and also `ForecastService/GetForecast`
(truth `not_registered`).

**HO5-2 (gap, Billing codes, behind HO5-1).** Billing handlers return a domain `*Fault{Kind}`;
`billing.FaultInterceptor` translates it with `errors.As(err, &f)` and
`status.Error(codeForKind[f.Kind], f.Detail)`, only for Billing methods (a method-prefix test). The
map index is a field of the `errors.As` target, not a constant or a parameter, so `codesFrom` cannot
read it (`auth_unknown`, reason "the code of `status.Error(codeForKind[f.Kind], f.Detail)` is
chosen at run time"). The module translation (D3, `sentTable`) reads sentinel tables and
`errors.Is` guards only, not `errors.As` on a typed error.

**HO5-3 (gap, VesselService, 8 missed pairs).** Vessel handlers return `fail(err)`:
`func fail(err error) error { var ae *apperr.Error; if errors.As(err, &ae) { return err }; return apperr.New(codes.Internal, ...) }`.
The pass-through `return err` returns the error parameter, which is unbound (as SG-3): reported
"the error `err` is not assigned in the function" (`internal/vessels/fail.go:16`), so the
`NOT_FOUND` (`repo.find`), `ALREADY_EXISTS` (`repo.insert`) and `PERMISSION_DENIED` (`mayTouch`)
codes the callers pass in are dropped; only the `INTERNAL` fallback is read.

**HO5-4 (precision, 13 of the 14 extras, BerthService).** Handlers call the translator directly,
`toStatus(ErrBadDay)` / `toStatus(err)`, where `toStatus(err error) error` is an `errors.Is` switch.
`errOfFunc(toStatus)` is memoised as the union of all its branches (`NOT_FOUND`, `INVALID_ARGUMENT`,
`UNIMPLEMENTED`, `INTERNAL`) and that union is given to every call, whatever sentinel is passed.
Same root as SG-3/HO5-3: the error parameter is not bound to the caller's argument. The 14th extra
(`ReservationService/GetReservation:INTERNAL`) is not diagnosed further here.

## What GA2 changes (static, generic) and what it leaves

1. SG-1: a local with a multi-value assignment is never single-valued in `codeOf`; a comma-ok map
   lookup `v, ok := m[k]` whose fallback is assigned under `!ok` is read as the map's value for a
   key found in the literal, the fallback otherwise.
2. SG-2: `siteField` follows constructor chains (a constructor returning another constructor's call
   or a local built by one), carrying string and code arguments, and reads `m[param]` over a package
   map literal with the bound key.
3. SG-3, HO5-3, HO5-4: an `error` parameter is bound, per call, to the errors of the caller's
   argument; `return <param>` under `errors.As(param, &x)` passes on only the status codes
   (a plain error cannot satisfy it), `x.GRPCStatus().Err()` on the `errors.As` target passes on
   the parameter's status codes, and `errors.Is(param, S)` branches are chosen by the bound
   sentinels.
4. HO5-1: `RegisterService(v.desc, v.impl)` where `v` ranges over a slice literal (directly or
   returned by a module function) registers each row's descriptor.
5. HO5-2 is attempted only after 1-4; it needs a typed-error translation table (`errors.As` + a map
   keyed by the target's field). PARK it if two honest attempts cannot read it generically.

Out of scope here, documented only:

* **Auth.** HO5 decides access by a bitmask intersection against a `policy` table
  (`auth.Gate.Unary`, `auth_not_literal`); SG's gate is `!publicMethods[info.FullMethod]` around the
  token check. Both stay `unclear` unless the existing auth reader classifies them; GA2 does not
  change the auth readers. Interceptor codes (E3d) reach rpcs only through a literal gate, so SG's
  68 interceptor pairs stay tied to auth unless the gate is read.
* **SG tables (0 of 9).** The schema is DDL in an embedded `.sql` file (`go:embed`) applied to MySQL
  at start-up; the importer has no reader for embedded DDL or for a MySQL schema
  (`tables_source: go`). Missing reader, not a code gap.

Observed-reply reconciliation (`lattice/spec/grpc.py` `reconcile_with`, 30/30 rpcs answered on HO5)
records one code per rpc from an empty request. It is considered only for a gap static reading
cannot close (step 3 of the node), and any code it adds is labelled observed, never extracted.
