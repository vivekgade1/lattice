# gRPC round 5, Stage B build (development data; nothing blind yet)

Pre-registration `2026-10-18-grpc-round5-prereg.md` with Amendments 1-2. Branch `np-grpc5-b`. S0 is its own commit (d3c889b).

**Verdict up front:** everything is built; every fixture probe and 101 of 101 mutants pass; **P5-BUDGET FAILS (Go group 924 lines vs 650)**;
REG shows 3 new failing frozen ids (declared, E3d); P5-BC has undeclared extract/RepoModel differences on kilnworks, harborline and
toolshed (now declared post hoc); the MySQL halves are NOT RUN.

## Built
S (pure-move split of `goextract_grpc.go` 2326 lines into three files; 80 top-level declarations identical; extract byte-identical
on 53 targets); E1 decorated ServiceDesc copies; E2 builder-consumed registration options; E3a-d GRPCStatus types, parameter-bound codes,
WithDetails, interceptor codes; E4a-c method-name uses fail safe to `auth_not_literal`, option audience gates, custom options;
E5 `database/sql` reader (248 lines); Q13 the D1.4 clause removed; L1/L1b directory entries in protected paths (rebuild and MCP guard),
L2 `[grpc] generate` run before the gates, L3 the brief names the protos and generator. Fixtures and truths for E1-E5 written and hashed
before any extractor edit (`freeze-dev5.sha256`).

## Probes (Arm 0 with --s0): 20 PASS, 3 FAIL, 2 NOT RUN
PASS: P5-SPLIT, CODE, LINKS, LEDGER, DESC, BOPT, STATUS, ICODE, AUD, SQL, PROT 5/5, GEN 3/3, BRIEF 1/1, ARMS, DET 10/10, NW, UNIT (161 tests; predicted
60-140), SCORE 88/88, PROC, FROZEN. FAIL: P5-BUDGET; REG; P5-BC (HTTP 73/73 identical, AST/H1b pass, but extract+RepoModel differ on three dev
corpora). NOT RUN: P5-HAPI and P5-REH MySQL halves (no driver environment); their SQLite halves pass.
Arm G (dev): toolshed RPC 1.0/1.0, SAFE 0, CODES recall 0.986/precision 1.0, AUTH 0 wrong 0 unclear (Login Public: the round-4 confident-wrong
fixed), TABLES 1.0, REC 1.0, truth flows replay 10/10 but only 7 recovered verified; harborline RPC 1.0/1.0, CODES 0.704/0.50, AUTH unclear 0.09;
kilnworks RPC 1.0/1.0, CODES recall 1.0 precision 0.70, flows 7 of 9 truth passing. Predictions for toolshed and harborline CODES precision
(0.95 and 0.90+) and harborline FLOWS were missed (reported only). Arm M PM-S5 18/18 identical, `select()` = gin. Arm L self-test 7/7 (stub agent;
the real run is separate).

## Mutants: 101 of 101 killed (40 of 40 new; 61 of 61 round-4 re-applied; control first and green).
Disclosures: GS5-4 is killed by P5-BC-AST only; GS5-2 only by test_sqlraw5; GH5-1 needed the probe verdict added to the comparison.

## Budgets
Go group 924 vs <= 650: MISS (gated FAIL). Others: sqlraw 248/300, proto 34/40, go_grpc.py 2/20, grpc.py 6/6, rebuild.py 82/90, guard.py 4/30.
Cost drivers: the helper-following option-read and per-site constructor machinery.
