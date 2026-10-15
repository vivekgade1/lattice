# Spike 2, the marketplace gap (git install, `--engine-file` on ask/draft, engines in one hub): pre-registration

## 1. Status

Date: 2026-10-15. Node G3, branch `np-market` (worktree `lattice-market`, from `next-phases` a4ab4a1). Written
BEFORE any code of this change exists; nothing of the new behaviour was run. Sources: the a4ab4a1 code
(modules, engines, spec CLIs, `cli.py`, Studio hub and server), the descriptor design, spike 2
prereg/results/trust probe, M-R seams, `tests/modules`, `pytest --collect-only` counts. One check of **git
itself** (not of Lattice), in scratch, Apple Git 2.54.0, `env -i` with `GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_GLOBAL=/dev/null GIT_ALLOW_PROTOCOL=file`, `git fetch --depth=1 --no-tags --no-recurse-submodules
file://<src> <sha>` into a `git init --bare --template=` repo: a reachable sha fetched; a sha made unreachable
(reset, `reflog expire --expire=now`, no gc) **also fetched** (protocol v2); `deadbeef...` failed `not our
ref`; the source's executable hooks, `uploadpack.packObjectsHook` and `core.fsmonitor` (each `touch <canary>`)
left the canary empty. Results: `2026-10-15-marketplace-results.md`, data
`docs/results/data/2026-10-15-marketplace/`. No LLM, no API key, no network (probe repos are `file://`).

**Scope.** (a) `lattice module install` from `git+file://` and `git+https://` (only with `--allow-remote`)
and from a git-repository index, pinned to a full commit sha; (b) `--engine-file` and `--modules` on `ask`
and `draft`; (c) per-project engine registries for several projects in one process. Files:
`lattice/modules/{__init__,__main__}.py`, new `lattice/modules/git.py`, `lattice/spec/engines.py`, the
parsers and docstrings of `lattice/spec/{ask,draft,__main__}.py`, `lattice/cli.py` usage, the descriptor
design doc (sections 2, 4), new `tests/modules/test_{git_install,market_index,engine_flags,hub_engines}.py`,
`dogfood/module_market_probes.py`, `dogfood/module_mutants.py` (MG added). **Outside the listed ownership,
needed (2.8):** line 130 of `tests/studio/test_engines_api.py`. `lattice/studio/{server,hub}.py` are NOT
edited. **Not done:** signing, publisher identity, a hosted index, version ranges or upgrades, ssh, private
repositories (no credentials), sandboxing adapter code, a web UI.

## 2. Decisions (settled before code)

**2.1 Sources.** Accepted: `git+file://<absolute path>@<sha>`, `git+https://<host>/<path>@<sha>`. The pin is
the text after the LAST `@`, exactly `[0-9a-f]{40}` or `[0-9a-f]{64}` (SHA-256 object format: the temp repo
is `init --object-format=sha256`). Refused BEFORE any subprocess, as `GitSourceNotSupported` (a
`ModuleError`; CLI `error: ...`, exit 2), first hit wins: (1) no pin, a branch or tag (`@main`, `@v0.1.0`),
an abbreviated sha, upper-case hex: `pin a full lowercase commit sha (git+<url>@<40 or 64 hex>); branch and
tag names are refused`; (2) other git forms (`git+ssh://`, `git@host:`, `git://`, `git+http://`, `ssh://`,
bare `https://...`): `only git+file:// and git+https:// sources are installed`; (3) userinfo
(`user[:pw]@host`), `?query`, `#fragment` (a credential would land in the lock and `ps`); (4) `git+https`
without `--allow-remote`: `remote sources need --allow-remote`; (5) a `git+file` path that is not a
directory. `resolve_source()` keeps its a4ab4a1 contract and message (local directories only; any git URL
-> `installing from a git URL is not implemented yet: ...`), so `tests/modules/test_modules.py` stays
byte-identical and passing; git sources go through the new `lattice.modules.git.fetch_tree()` from `install()`.

**2.2 Trust model.** The pinned commit sha is the identity: whoever can write the source repository (or the
index repository at its pinned sha) chooses what it names; a moved branch or force-push cannot change it.
Integrity after install is unchanged: the installed tree's sha256 in the lock, re-hashed on every load.
Install executes NO module content: no checkout, no import of an adapter or `entry` (the clash check uses
descriptor data). A module naming `adapter = "module:Class"` or a custom `entry` keeps today's rules: the
summary says CODE; the code runs, unsandboxed, when used. Not claimed: publisher authenticity, harmless
descriptor data (it can name any Docker image), safety of git itself.

**What git can do to this machine, and the answer.** (i) *Fetcher config* (system, global, XDG,
`include.path`): `url.<x>.insteadOf`, `credential.helper`, `core.sshCommand`, `core.fsmonitor`,
`core.hooksPath`, `trace2.*Target`, `protocol.*.allow`: none is read (`GIT_CONFIG_NOSYSTEM`,
`GIT_CONFIG_GLOBAL=/dev/null`, `HOME`/`XDG_CONFIG_HOME` empty). (ii) *Environment* (`GIT_DIR`,
`GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`, `GIT_SSH_COMMAND`, `GIT_EXEC_PATH`, `GIT_TRACE*`): the env is
built from scratch (2.3). (iii) *Hooks*: never transferred; the temp repo has none (`--template=`),
`core.hooksPath=/dev/null`, no ref updated. (iv) *`.gitattributes`, smudge/clean/process filters, `eol`,
`ident`, `working-tree-encoding`, LFS, `post-checkout`*: act at checkout; there is NO checkout (bare repo,
blobs read by `cat-file`), so unreachable by construction; `.gitattributes`, `.gitmodules`, `.lfsconfig` at
any depth are also **dropped** from the installed tree and listed. (v) *Submodules* (`ext::` URLs,
CVE-2024-32002): no recursion; `protocol.allow=never` but the one transport. (vi) *Malformed objects, `..` or
`.git` names*: `transfer.fsckObjects=true` refuses them at fetch; 2.4 refuses them again. (vii) *The file://
source side*: git runs `upload-pack` on the SOURCE repo, reading its config; git honours
`uploadpack.packObjectsHook` only from protected config; this trusts git, so git < **2.45.1**
(CVE-2024-32002/-32004/-32465) is refused before fetching. (viii) *Resources*: depth 1, timeouts, caps (2.4);
the download is bounded only by the timeout (risk 5).

**2.3 The fetch** (`lattice/modules/git.py`; `subprocess.run([git, ...])`, never a shell). Env, exactly:
`PATH`, `TMPDIR` (inherited), `HOME`=`XDG_CONFIG_HOME`=`<tmp>/home` (empty), `GIT_CONFIG_NOSYSTEM=1`,
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS`=`SSH_ASKPASS`=absolute `false`,
`GIT_LFS_SKIP_SMUDGE=1`, `GIT_NO_LAZY_FETCH=1`, `GIT_NO_REPLACE_OBJECTS=1`, `GIT_ATTR_NOSYSTEM=1`,
`GIT_OPTIONAL_LOCKS=0`, `GIT_PROTOCOL_FROM_USER=0`, `GIT_ALLOW_PROTOCOL=file` (`https` with `--allow-remote`,
plus inherited `https_proxy`/`HTTPS_PROXY`/`no_proxy`), `LC_ALL=C`. Every call: `-c core.hooksPath=/dev/null
-c core.fsmonitor=false -c core.symlinks=false -c core.protectHFS=true -c core.protectNTFS=true -c
protocol.allow=never -c protocol.<file|https>.allow=always -c submodule.recurse=false -c
fetch.recurseSubmodules=false -c transfer.fsckObjects=true -c fetch.fsckObjects=true -c gc.auto=0 -c
maintenance.auto=false -c credential.helper= -c http.followRedirects=false`, `stdin=DEVNULL`, own session,
killed on timeout (fetch 120 s, others 20 s). Sequence: `git --version` (>= 2.45.1);
`mkdtemp(prefix="lattice-module-")`; `git init -q --bare --template= [--object-format=sha256] <tmp>/repo`;
`git -C <repo> fetch -q --depth=1 --no-tags --no-recurse-submodules --no-write-fetch-head <url> <sha>` (url
= the source without `git+` and `@<sha>`); `count-objects -v` size-pack <= 32 MiB; `cat-file -t <sha>` ==
`commit` (else `<sha> is a <type>, not a commit`); `ls-tree -r -z --full-tree --long <sha>`; 2.4 on that
listing BEFORE any blob is read; `cat-file --batch` writes the blobs to `<tmp>/tree`. A git failure (`not our
ref`, fsck) -> `ModuleError: git fetch of <source> failed: <last stderr line>`, exit 2. The temp dir is
removed in `finally` (success, refusal, exception, KeyboardInterrupt).

**2.4 Materialising.** Modes `100644`, `100755` -> a regular file, `0o644`, opened
`O_CREAT|O_EXCL|O_NOFOLLOW`; `120000` -> `<path> is a symbolic link (mode 120000): refused`; `160000` ->
`<path> is a submodule (mode 160000): refused`. Every path component matches `[A-Za-z0-9][A-Za-z0-9._+-]*`
(so no `.`, `..`, `.git`, leading dot or `-`, space, non-ASCII), at most 8 components and 200 bytes; no two
paths equal under `casefold()` (`differ only in case: refused`); the three dropped names are the only
leading-dot exception. Caps from `ls-tree` sizes: 200 files, 1,048,576 B per blob, 8 MiB total. Any violation
refuses the WHOLE install. Then `<tmp>/tree` takes the a4ab4a1 directory path unchanged (`read_manifest`,
index name/version/sha256, `tree_hash`, already installed, engine clash, `describe`, confirm, copy, re-hash).

**2.5 Install, lock, atomicity.** `lattice module install SOURCE [SOURCE ...] [--index DIR|URL]
[--allow-remote] [--yes] [--project DIR]`. All sources are resolved, fetched, validated, hashed and
clash-checked (installed modules, built-ins, and each other: `module <n> appears twice in this install`)
before ONE confirmation. One source: a4ab4a1's output and question; N: N summaries, then `Install N modules
(a 0.1.0, b 1.0) into <root>? [y/N] `. A git summary adds after `source:` the line `commit:   <sha> (fetched
without hooks, filters, submodules, LFS; <n> files, <b> bytes)` and, if any, `dropped:  <names>`. Write:
each tree copied to `.lattice/modules/.staging-<pid>/<name>`, re-hashed, renamed to `.lattice/modules/<name>`;
then the lock written to `lattice.modules.lock.tmp` and `os.replace`d (directory installs too: same bytes,
now atomic). Any failure removes the staging dir and every renamed dir; the lock is unchanged. Lock key
order `name, version, kind, sha256, source`, then for git only `commit` and, through an index, `index`
(`source` = the git source with `@<sha>`; `index` = the index path or URL). Directory entries gain no key, so
their lock bytes equal a4ab4a1's; `LOCK_VERSION` stays 1; a4ab4a1's `read_lock` ignores extra keys. `info`
prints `  commit:          <sha>` after `source:` for git entries only. Exit codes unchanged.

**2.6 Index.** `--index DIR` as a4ab4a1, plus: an entry's `source` may be a pinned git source (an unpinned
one stays `GitSourceNotSupported`, as the a4ab4a1 test expects). `--index git+file://...@<sha>` (or
`git+https` with `--allow-remote`): fetched by 2.3-2.4 and read as a directory of `<name>.toml`; in a git
index every `source` must be a pinned git source (`a git index names pinned git sources only`). An entry's
`sha256` is verified when present; `name`/`version` must equal the manifest's (a4ab4a1 messages).

**2.7 ask and draft.** Both parsers gain `--engine-file FILE` (append; check's help text) and `--modules DIR`
(draft: in the `incremental` group). They matter only with `--verify`/`--impact` and are otherwise accepted
and harmless, as `--database` is. `ask.main` and `draft.main`, after dropping the command word and BEFORE
building the parser, call check's `_preload_engines(argv)`; a problem prints check's line
(`error: EngineError: ...` / `error: ModuleError: ...`) and returns 2. Covers `lattice spec ask|draft`,
`python -m lattice.spec.ask|draft`, `lattice.spec.__main__.main(["ask", ...])`; Studio and MCP unchanged.

**2.8 Several projects in one process.** In `engines.py` the process registry becomes `_BASE` (built-ins,
then CLI `--engine-file`/`--modules`); `REGISTRY` becomes a proxy with the same methods (`names entries get
entry by_scheme add remove scoped`) delegating to `_ACTIVE.get() or _BASE` (`_ACTIVE` a `ContextVar`);
`scoped()` also restores `_ACTIVE`; new `project_registry()` (built-ins only) and `using(reg)`.
`load_project_engines(root, table)` builds a fresh project registry in a fixed order (built-ins, `[engines]
paths` as listed, verified modules by name), returns it as `ProjectEngines.registry`, **activates it in the
calling context** (`_ACTIVE.set`, no reset) and never touches `_BASE`. So A and B may both declare
`mariadb` differently; A's engines are invisible to B; B's registry does not depend on load order. Studio's
three sites (`check_kwargs`, `verify_config`, `get_engines`) already load right before their lookups in the
same thread, so `server.py`/`hub.py` need no edit. A thread with no active registry (spawned inside a check;
a reused pool thread before its first load) sees `_BASE`: built-ins only in Studio, fail closed. Code
adapters: when the descriptor's directory holds `<top>.py` or `<top>/__init__.py` and `sys.modules[<top>]`
came from another file, `_adapter_class` raises `engine 'x': adapter module '<top>' is already imported from
<file>; give this engine's adapter module a distinct name` (a4ab4a1 silently returned the other project's
class). Semantics change: project engines are no longer process-wide, so `test_engines_api.py:130`
(`REGISTRY.entry("mariadb").source == "project"` in the TEST thread after a request) becomes `... is None`
with a comment; no other existing test changes. The CLI (one project per process) keeps `_BASE`, unchanged.

## 3. Probes (blind)

Each deterministic probe runs **10 times** on fixed paths under the scratchpad (`<root>` = `.../g3`),
`TMPDIR=<root>/tmp`; pass = 10 of 10 byte-identical (stdout, stderr, exit, lock bytes, installed tree with
sha256s) plus its rule. Fixture repos: fixed identity, `GIT_AUTHOR_DATE`=`GIT_COMMITTER_DATE`=
`2026-10-15T00:00:00Z`, `commit.gpgsign=false`, so shas repeat. A `git` shim first on `PATH` logs argv and env
keys, then execs git. `<root>/canary/` is the target of every hostile action: "nothing executed" = empty.
"Nothing written" = the project's listing and hashes unchanged, no `lattice-module-*` in `TMPDIR`. Fixture
`mariadb-engine` = a4ab4a1's test module (manifest, `engine.toml` = the example `mariadb.toml`, `README.md`).

**M-git.** Repo `repos/maria`: C1 = the fixture at 0.1.0, C2 = 0.2.0. **a** `install
git+file://<root>/repos/maria@<C1> --yes`: exit 0; `.lattice/modules/mariadb-engine/` = the 3 files, bytes =
`git show C1:<path>`; sha256 = that of a directory install of `git archive C1`; lock = `{"version": 1,
"modules": [{name, version "0.1.0", kind "engine", sha256, source "git+file://<root>/repos/maria@<C1>",
commit "<C1>"}]}` (indent 2, final newline); `list` `ok`; `info` has `commit:`; `register_modules` registers
`mariadb`; `uninstall` removes dir and lock. Shim: exactly 7 calls (`--version`, `init`, `fetch`,
`count-objects`, `cat-file -t`, `ls-tree`, `cat-file --batch`), each with every 2.3 `-c` pair and env; 0
`clone`/`checkout`/`submodule`/`lfs`; every URL `file://`. **b** `@<C2>`, second project: `0.2.0`. **c** 11
refusals, 0 git calls, 2.1's message each: no `@`, `@main`, `@v0.1.0`, `@<C1[:7]>`, upper-case C1,
`git+ssh://h/r@<C1>`, `git@h:r`, `git+http://h/r@<C1>`, `git+https://user:pw@h/r@<C1>`,
`git+https://example.invalid/r@<C1>` without `--allow-remote`, `...@<C1>#x`. **d** the latter with
`--allow-remote`: one fetch argv with `https://`, `GIT_ALLOW_PROTOCOL=https`; the shim fails it; exit 2 `git
fetch ... failed`. **e** a under `sandbox-exec -p "(version 1)(allow default)(deny network*)"`: = a. Rule: 5 of 5.

**M-tamper.** Each row as predicted; canary empty; refusals write nothing. Rule: 16 of 16.
| Row | Fixture | Predicted |
|---|---|---|
| T1 | pin `deadbeef` x5 (absent) | 2, `git fetch ... failed: ... not our ref ...` |
| T2 | dir index entry, pinned git source, `sha256` = 64 zeros | 2, `pins sha256 0000..., the source tree hashes to ...: refused` |
| T3 | after M-git a, `main` advanced to C2; `@<C1>` into a fresh project | 0, 0.1.0, sha256 = M-git a's |
| T4 | C1 unreachable (reset to a rewritten C1', reflog expired); a: no gc; b: `gc --prune=now` | a: 0, = M-git a; b: 2, `not our ref` |
| T5 | `link -> /etc/passwd` (120000) | 2, `link is a symbolic link (mode 120000): refused`; 0 blobs read |
| T6 | entries `..`, `.git/config`, `.GIT/hooks/x` (`hash-object -t tree --literally`) | 2, the fetch fails on fsck (`hasDotdot`/`hasDotgit`) |
| T7 | gitlink `sub` (160000) + `.gitmodules` `url = ext::sh -c touch <canary>/sub` | 2, `sub is a submodule (mode 160000): refused` |
| T8 | a: a 2 MiB blob; b: 201 files; c: 9 x 1,000,000 B; d: a 40 MiB random blob | 2: `(cap 1048576)`; `201 files (cap 200)`; `9000000 bytes (cap 8388608)`; `size-pack ... (cap 32 MiB)` |
| T9 | source repo: executable hooks (post-checkout, post-merge, reference-transaction, pre-commit, fsmonitor-watchman), config `core.hooksPath`, `core.fsmonitor`, `uploadpack.packObjectsHook`, `filter.evil.{smudge,clean,process}`, each `touch <canary>/...` | 0, installs |
| T10 | tree has `.gitattributes` `* filter=evil eol=crlf`, `.lfsconfig`; the user's `GIT_CONFIG_GLOBAL` file sets `filter.evil.smudge`, `core.hooksPath`, `credential.helper`, `trace2.eventTarget=<canary>/t2`, `url."file://<root>/repos/evil".insteadOf` the real URL | 0; both files absent and listed `dropped:`; `engine.toml` = the blob |
| T11 | user env `GIT_TRACE=<canary>/trace`, `GIT_DIR=<canary>/gd`, `GIT_CONFIG_COUNT=1` (`core.hooksPath`), `GIT_CONFIG_PARAMETERS`, `GIT_SSH_COMMAND` | 0, installs |
| T12 | `adapter = "evil:Adapter"`, `evil.py` writes `<canary>/import` at import; a `custom` module `entry = "evilentry"` (same) | 0 each; summary says `CODE` |
| T13 | `Engine.toml` and `engine.toml` | 2, `differ only in case: refused` |
| T14 | pin = an annotated tag's object sha; a tree sha | 2, `is a tag, not a commit`; `is a tree, not a commit` |
| T15 | names ` a.toml`, `é.toml`, `-x`, `.github/x` | 2 each (2.4) |
| T16 | the shim answers `git version 2.39.0` | 2, `older than 2.45.1 ...`; no fetch |

**M-index.** Repos `a` (engine `mariadb`), `b` (custom, no entry), `c` (a symlink); index repo `idx` at X with
`a b c .toml`, `d.toml` (= `a` with a wrong `sha256`), `e.toml` (`source = "../a"`). **I1** `install a b
--index git+file://<root>/repos/idx@<X> --yes`: 0; 2 lock entries by name, each with `commit` and `index`.
**I2** `a b c`: 2, nothing written. **I3** `a b d`: 2, nothing written. **I4** a installed, then `install a
b`: 2 `already installed`, b absent, lock byte-identical. **I5** `a a`: 2 `appears twice`. **I6** a dir index
naming a pinned git source: 0; unpinned: `GitSourceNotSupported`. **I7** `e`: 2 `a git index names pinned git
sources only`. **I8** `--index ...idx@main`: 2 (2.1). **I9** (pytest) the 2nd rename raises: the 1st is
removed, no `.staging-*`, lock unchanged. Rule: 9 of 9.

**M-flag.** `check`, `ask`, `draft`, each as `python -m lattice.spec <cmd>`. **F1** `--engine-file
mariadb.toml --help`: `--database` choices `{sqlite,postgres,mysql,clickhouse,mariadb}`. **F2** (in-process)
`_preload_engines`, then `_parser().parse_args([... "--database", "mariadb", "--engine-file", F])`:
`database == "mariadb"`, `engine_file == [F]`. **F3** unknown field `bogus`; **F4** missing file; **F5**
`name = "mysql"`; **F6** `--modules P`, P's module tampered; **F7** `--engine-file=F`; **F8** `--database
mariadb` alone (`invalid choice`). Rule: 24 of 24 cells; F3-F6 exit 2 with the first stderr line
byte-identical across the three; F8 exit 2 in all. Baseline a4ab4a1 (blind): F1 ask/draft lack `mariadb`;
F3-F6 ask/draft fail as `unrecognized arguments`, not with check's line.

**M-hub.** A, B by `tests.studio.test_server.make_project(..., app_limit=5)`; A: `[engines] paths =
["engines/mariadb.toml"]` (`mariadb:11`) and module `pg-a` (engine `pga`); B: `mariadb.toml` with
`mariadb:10.11`. One `StudioHub` HTTP server. **H1** `GET /p/a/api/engines`, `/p/b/...` in orders ABAB,
BABA, AABB: each body identical in every order and to the project served alone; B: `[sqlite, postgres,
mysql, clickhouse, mariadb]`, image `mariadb:10.11`, `errors == []`; A lists `pga`. **H2** one thread:
`hub.get("a").harness.check_kwargs()`, then `engines.find("mariadb").server.image == "mariadb:11"`, `pga`
found; then B's: `mariadb:10.11`, `find("pga") is None`, `pga` not in `database_names()`; `_BASE.names()` =
the 3 built-ins throughout. **H3** 8 threads, 200 interleaved requests: each body = H1's. **H4** A and B each
with `adapter.py` (different `FakeDb`): A resolves; B raises 2.8's message. **H5** static: the 3
`load_project_engines` sites in `server.py` precede their lookups in the same function. Rule: 5 of 5.
Baseline a4ab4a1 (blind): H1 B's errors hold `already registered from <A>/engines/mariadb.toml with a
different descriptor` when A loads first; H2 B gets `mariadb:11` and `pga`; H4 B silently gets A's class.

**M-regress.** **R1** one CLI script on fixed paths: directory install (refused without `--yes` at a non-tty,
then `--yes`), `file://` install, dir-index install, `list [--json]`, `info [--json]`, a 1-byte tamper then
`list` (exit 1), `uninstall`: stdout, stderr, exits, lock bytes, trees byte-identical between a4ab4a1 (`git
archive a4ab4a1 lattice docs/examples | tar -x` in scratch) and the worktree. **R2** a4ab4a1's `module list`
and `info` on M-git a's project: `ok`. **R3** `git diff a4ab4a1 -- tests/modules/test_modules.py` empty, 26
passed; `tests/spec/test_engines.py` 60, `tests/studio/test_projects.py` 24, `tests/test_cli.py` 21,
`tests/spec/test_ask.py` 34, `tests/spec/test_draft.py` 31 unchanged and passing; `test_engines_api.py` 5
with only line 130 changed; MM1-MM5 still caught; `dogfood/studio/check.sh` PASS, 435 cases. **R4**
`lattice module --help`, `lattice --help` differ from a4ab4a1 only by new options and usage. Rule: 4 of 4.

## 4. Mutation plan

`dogfood/module_mutants.py` (scratch copy; SUITE gains the four new test files and the probes). MG1 a
branch/tag accepted as the pin -> M-git c, I8. MG2 the git env inherits `os.environ` -> T11 (`GIT_TRACE`
canary). MG3 `GIT_CONFIG_GLOBAL`/`NOSYSTEM` unset -> T10 (`trace2` canary, `insteadOf`). MG4 a symlink
written as a file holding its target -> T5. MG5 blob cap 1 MiB -> 100 MiB -> T8a. MG6 file cap dropped ->
T8b. MG7 `cat-file -t` unchecked -> T14 (a tag peels to its tree and installs). MG8 temp dir kept on refusal
-> every T row. MG9 the lock written per module -> I2, I3, I4. MG10 `.gitattributes` kept -> T10. MG11 lock
without `commit` -> M-git a. MG12 a directory entry gains `"commit": null` -> R1. MG13 `--allow-remote` gate
dropped -> M-git c. MG14 `load_project_engines` registers into `_BASE` (a4ab4a1) -> H1, H2. MG15 `scoped()`
leaves `_ACTIVE` -> `test_hub_engines`. MG16 ask's preload skipped -> F1, F2. MG17 the adapter `sys.modules`
check removed -> H4. MG18 a git index accepts a relative `source` -> I7. MG19 `transfer.fsckObjects`
dropped -> T6 (2.4 refuses with another message). MG20 `-c core.hooksPath=/dev/null` dropped: predicted to
SURVIVE every behavioural probe (no hook exists to run) and die only by the exact-argv test of
`test_git_install.py`. Rule: 20 of 20 caught as predicted; MM1-MM5 still caught; survivors reported.

## 5. Named risks, and what the experiment cannot show

1. **Git is the attack surface**: pack parsing, fsck and the file:// `upload-pack` on the source are git's
   code; one git (Apple Git 2.54.0, macOS 27) is tested; a future CVE is not covered by the 2.45.1 floor.
2. **The pin is SHA-1** for most repos (git's collision-detecting SHA-1; SHA-256 repos accepted); from
   install on, the tree sha256 is Lattice's own pin.
3. **Install is not trust**: descriptors run Docker images of the publisher's choosing; CODE adapters and
   entries run unsandboxed on use; git puts untrusted modules one command away; no signing or publisher
   identity; an index is as trustworthy as whoever wrote its pinned commit.
4. **Hub isolation is a convention**: every Studio path that resolves an engine must load the project's
   engines first in the same thread (3 sites today, H5); a new path skipping it in a reused pool thread sees
   the previous project's registry. Callers relying on process-wide project engines break (2.8).
5. **Caps after download**: size-pack is checked after the fetch; a hostile source fills `TMPDIR` until the
   120 s timeout. The caps are arbitrary: a legitimate module over 200 files or with dot-dirs is refused.
6. **Builder bias**: the builder reads these predictions; fixtures follow the stated rules; the probe script
   is audited against this document.

**Cannot show.** `git+https` against a real host (TLS, redirects, proxies, download size: M-git d proves only
the gate and argv); hostile vectors beyond the 16 rows (an empty canary proves only what was tried); Linux,
other git versions, case-sensitive filesystems; more than two projects; real Docker checks of A's and B's
MariaDB (M-P2 stays n=1); a third-party adapter; concurrency beyond H3; whether owners find sha pins usable.

## 6. Blind vs post hoc; verification

Every prediction above is blind; T4a rests on the git-only check of section 1 and is labelled so. A failed
prediction is a finding, never silently fixed; a fix it motivates is a later, labelled post-hoc commit and
the blind numbers stay as first measured. Ambiguities in a4ab4a1, settled here: `resolve_source` keeps
refusing git (2.1); `GitSourceNotSupported` now also names refused git forms; the lock write becomes atomic
(2.5); `test_engines_api.py:130` asserts the leak this node removes (2.8).
```
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-market
uv run pytest tests/modules tests/spec/test_engines.py tests/studio/test_engines_api.py \
  tests/studio/test_projects.py tests/test_cli.py tests/spec/test_ask.py tests/spec/test_draft.py -q -p no:cacheprovider
uv run python dogfood/module_market_probes.py   # M-git..M-regress -> docs/results/data/2026-10-15-marketplace/
uv run python dogfood/module_mutants.py         # control, MM1-MM5, MG1-MG20
dogfood/studio/check.sh                         # PASS, 435 cases
```
Pass: every rule of section 3; section 4 as predicted; `git diff a4ab4a1 --stat` only in section 1's files
(plus `test_engines_api.py`, one line); 0 network (every shim URL `file://` but M-git d's shim-failed https
argv); nothing committed by the probes; no write outside the worktree and the scratch root.

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

**A. Sign-off.** The single assertion at `tests/studio/test_engines_api.py:130` (a project's engine is
visible process-wide: the leak this change removes) is changed to assert isolation (`is None`). That one
assertion and nothing else in the file; disclosed in the results as a test change with its reason.

**B. Hub isolation fails CLOSED.** The per-thread "active" registry is activated by a SCOPED mechanism
(a context manager over a `contextvars.ContextVar`, or equivalent) that restores the previous value on
exit, including on exceptions. A thread or task that never activated a project's registry sees the
BUILT-IN engines only, never the registry of a project another request loaded earlier. Reason: the
failure mode of the draft design (a reused pool thread seeing the previous project's registry) is the
very leak being fixed. Consequences: new probe **M-hub6** (a worker thread that never loaded sees only
the built-ins, after another thread loaded project A; 10 runs identical; predicted: built-ins only, A's
engine absent, no error) and a pooled-thread variant (the same thread serves A then B: B never sees A,
and after the scope exits the thread sees built-ins only); new mutant **MG21** "activation not
restored on scope exit" and **MG22** "unactivated lookup falls back to the last loaded registry",
each killed by M-hub6. R5 (the convention risk) is reduced, not removed: a code path that needs
project engines and forgets to activate now fails with the engine-not-found error instead of silently
resolving the wrong project's engine, which the results report as a known behaviour.
