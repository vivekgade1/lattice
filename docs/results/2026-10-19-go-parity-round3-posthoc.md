# Go parity round 3: post hoc diagnosis and fixes (development data, NOT blind)

Source: the post hoc node's report on `np-go3-posthoc`; all numbers are post hoc.

**H-PI3 (1 of 119 verified): a library defect.** The Go extractor names a login route only when a Public POST calls `bcrypt.CompareHashAndPassword`
(`goextract_assemble.go` `buildAuth`; `goextract_owner.go` sets the bcrypt use only on that call); the held-out service compares the plain stored password with
`crypto/subtle.ConstantTimeCompare`, so `AuthModel.login_path` was None and every authenticated probe answered 401. The harness and `studio.toml` are standard.
The HARNESS_API/BRIEF gap is real but secondary: it required a plain stored password and never said how the importer finds the login.
**Strict-control failure:** GO801 fired on five Book routes (table Shelf) as a false positive: the owner field was read in a module helper
(`return book, shelf.UserID, nil`) and compared in the handler (`owner != userID(c)`), a flow `mentionsColumn` did not follow. **Third defect:** the table name
`shelfs` vs GORM's `shelves` (jinzhu/inflection `([lr])f$` and `([^f])fe$` rules).
**Fixes (6 library files, +152/-5; 15 tests, 8 fail on the pre-fix source):** a plain-password login recognition (four compare forms; bcrypt still wins; only when the Public POST body has a
password field), the GORM plural rules, and GO801 evidence through a helper-returned owner field (it only removes findings). **Post hoc H-PI3 on S4:** 1 -> 45 (login) -> 58 verified (plural),
share 0.0084 -> 0.4118 -> **0.4958** (the floor is 0.50: still a hair under; refuted 0). Final layer-A baseline `[]`; 14 of 15 routes and 3 of 4 tables verified.
**Byte-compat offline:** old vs new extractor and layer A on 53 Go modules: only `shelfie` differs. Tests: `tests/gocheck` 160, `tests/importer` 861 passed; the full run shows 2 failures
(`test_outside` as in the base; `test_multidb` Docker) and 30 driver errors, none involving Go.
**Held-out mutants, post hoc, scratch tree and ledger:** the control now passes. With the frozen runner and the fixed library: 8 of 16 killed (HM5, 6, 9, 10, 11, 14, 15, 16). The runner's
`classify_operator` reads "(removes the 403 owner check)" as the HM9-10 operator instead of the owner-filter operator, so HM1-4 are scored as survivors although layer A found
`GO801|GET /shelves/:id`, `DELETE /shelves/:id`, `DELETE /notes/:id`; with a scratch-only classifier correction: 11 of 16 killed (class gates HM1-4 3/4 pass, HM5-6 2/2, HM9-10 2/2, HM7-8 0/2 fail); the
overall gate (>= 13) is not met. Survivors: HM2 (GO801 stays silent because the notes `Where` mentions `user_id`: a documented limit), HM7-8 (the R1 item goes verified to unexercised), HM12-13
(the 409 items are unexercised). **Not run:** the 18 library mutants, the Streak M-R1 row, `owner_via` inference, any fix of the frozen runner or brief.
