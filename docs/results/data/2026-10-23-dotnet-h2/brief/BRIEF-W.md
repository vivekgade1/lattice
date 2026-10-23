You write 16 mutants of one C# service. You are given the service (the module directory under `tests/spec/`, with its
harness beside it) and its `truth.json` only. You never run Lattice, never read anything outside this directory, and never
read any checker's results. Each mutant is a patch against the module that builds with 0 errors and changes behaviour in
exactly one way.

Toolchain (export before any command; every file the toolchain writes stays under `./scratch`):

```sh
export PATH=/opt/homebrew/bin:$PATH DOTNET_ROOT=/opt/homebrew/opt/dotnet/libexec DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
export HOME=$PWD/scratch/home DOTNET_CLI_HOME=$PWD/scratch/home NUGET_PACKAGES=$PWD/scratch/home/.nuget/packages TMPDIR=$PWD/scratch/.tmp
mkdir -p "$HOME" "$TMPDIR"
```

Build each mutant in a copy under `./scratch` (never inside `tests/`): copy the module, apply the patch there with
`patch -p1`, then `dotnet build <the .csproj> -c Release -p:RestoreLockedMode=true`. Leave `tests/` exactly as you found it.

Instance rule: in `truth.json` order, take the first and the last route that fits the operator. When only one route fits
an operator, use it once and give the next operator three instances. Record every substitution.

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

Deliver exactly these 17 files in the directory `./mutants/` (these names, nothing else in it):

- `mutants/MUTANTS.md`: one line per mutant, exactly
  `HMn | <operator> | METHOD /path | file:line | substitutions`
  (the route template exactly as in `truth.json`; `file:line` relative to the module directory);
- `mutants/m01.diff` ... `mutants/m16.diff` (two digits: `m01.diff` is HM1, `m16.diff` is HM16): each a unified diff
  that applies with `patch -p1` run inside the module directory, and builds with 0 errors.

If an operator truly has no fit in this service, write `HMn | <operator> | NONE | <the reason>` for that id and deliver no
diff for it; do not substitute another operator. Your spending is capped: finish the patches, then `MUTANTS.md`.
