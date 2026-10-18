"""Unit tests of the S3 runners (``dogfood/go_parity_s3.py``, ``dogfood/go_parity_s3_mutants.py``): the S3
expectation tables on small synthetic inputs, the structural Go edits of JM31-JM34 on synthetic Go source
and on the goginapp stand-in, and the self-hash check. They never read ``tests/spec/gotickets``."""

# ruff: noqa: E501
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "dogfood"))
sys.path.insert(0, str(REPO))

import go_parity_probes as P1  # noqa: E402
import go_parity_s3 as S  # noqa: E402
import go_parity_s3_mutants as M  # noqa: E402

# ---- T-S3 tables ---------------------------------------------------------------------------------


def good_inferences():
    ticket = {"rule": "owner_uses", "table": "Ticket", "column": "reporter_id",
              "evidence": [f"code:api/api.go:{n}" for n in range(10, 17)]}  # fmt: skip
    handoff = {"rule": "owner_uses", "table": "Handoff", "column": None,
               "columns": ["from_user_id", "to_user_id"], "evidence": ["code:api/api.go:90"],
               "unclear": True}  # fmt: skip
    parse = [{"rule": "path_parse", "route": "x", "param": "p", "evidence": ["code:a:1"]}] * 5
    return [ticket, handoff, *parse,
            {"rule": "bind_status", "status": 422, "evidence": ["code:a:2"]},
            {"rule": "status_facts", "table": "Ticket", "column": "status",
             "choices": ["open", "closed"], "evidence": []},
            {"rule": "json_dash", "table": "User", "columns": ["password_hash"]}]  # fmt: skip


HOWS = {("api/api.go", 10): "assign", **{("api/api.go", n): "compare" for n in range(11, 17)}}
OWNERS = {"User": None, "Queue": None, "Ticket": "reporter_id", "Handoff": None}


def test_pi1_is_exact_against_t_s3():
    routes = dict(S.T_S3_ROUTES)
    assert S.score_pi1("gin", routes, 11, set(S.T_S3_TABLES))
    assert len(routes) == 11 and sum(a == "Public" for a in routes.values()) == 3
    assert not S.score_pi1("gin", {**routes, ("GET", "/me"): "Public"}, 11, set(S.T_S3_TABLES))
    assert not S.score_pi1("gin", routes, 10, set(S.T_S3_TABLES))
    assert not S.score_pi1("fastapi", routes, 11, set(S.T_S3_TABLES))
    assert not S.score_pi1("gin", routes, 11, {"users", "queues", "tickets"})
    assert not S.score_pi1("gin", {**routes, ("GET", "/me"): "Member"}, 11, set(S.T_S3_TABLES))


def test_pi2_wants_seven_evidence_lines_an_unclear_handoff_and_ten_inferences():
    ok, ev = S.score_pi2(good_inferences(), HOWS, OWNERS)
    assert ok and ev["inferences"] == 10 and ev["how"] == ["assign"] + ["compare"] * 6
    inf = good_inferences()
    inf[0] = {**inf[0], "evidence": inf[0]["evidence"][:6]}
    assert not S.score_pi2(inf, HOWS, OWNERS)[0]  # 6 lines
    inf = good_inferences()
    inf[1] = {**inf[1], "columns": ["from_user_id"]}
    assert not S.score_pi2(inf, HOWS, OWNERS)[0]  # Handoff names one column only
    inf = good_inferences()
    del inf[1]
    assert not S.score_pi2(inf, HOWS, OWNERS)[0]  # not unclear, and 9 inferences
    assert not S.score_pi2(good_inferences()[:-1], HOWS, OWNERS)[0]
    assert not S.score_pi2(good_inferences(), HOWS, {**OWNERS, "Queue": "creator_id"})[0]
    assert not S.score_pi2(good_inferences(), HOWS, {**OWNERS, "User": "id"})[0]
    assert not S.score_pi2(good_inferences(), HOWS, {**OWNERS, "Ticket": None})[0]
    wrong = {**HOWS, ("api/api.go", 11): "assign"}
    assert not S.score_pi2(good_inferences(), wrong, OWNERS)[0]
    inf = good_inferences()
    inf[-3] = {"rule": "bind_status", "status": 400, "evidence": []}
    assert not S.score_pi2(inf, HOWS, OWNERS)[0]


def test_pi3_ranges_are_reported_with_the_predicted_bounds():
    def items(v, r, u):
        out = {}
        for k, (n, w) in enumerate([(v, "verified"), (r, "refuted"), (u, "unexercised")]):
            out.update({f"{w}{k}{i}": {"verdict": w} for i in range(n)})
        return out

    assert S.score_pi3(items(40, 1, 19)) == {"items": True, "share": True, "refuted": True}
    assert S.score_pi3(items(40, 4, 16))["refuted"] is False
    assert S.score_pi3(items(30, 0, 30))["share"] is False
    assert S.score_pi3(items(87, 0, 0)) == {"items": False, "share": False, "refuted": True}


def machine(edges, table="Ticket", column="status"):
    return {"kind": "machine", "status": "pending", "sentence": "m",
            "spec_object": {"type": "Machine", "table": table, "column": column,
                            "edges": edges, "initial": ["open"]}}  # fmt: skip


def test_pi5_needs_the_open_to_closed_edge_and_pending_proposals():
    inv = {"kind": "invariant", "status": "pending", "spec_object": {}}
    ok, ev = S.score_pi5([inv, machine([["open", "closed"], ["closed", "open"]])])
    assert ok and ev["machine_tickets_status"]["closed_to_open"] and ev["in_predicted_range"]
    ok, ev = S.score_pi5([inv, machine([["open", "closed"]])])
    assert ok and not ev["machine_tickets_status"]["closed_to_open"]  # reported, not gated
    assert not S.score_pi5([inv, machine([["closed", "open"]])])[0]
    assert not S.score_pi5([inv, machine([["open", "closed"]], table="Run")])[0]
    assert not S.score_pi5([inv, machine([["open", "closed"]], column="kind")])[0]
    assert not S.score_pi5([inv, {**machine([["open", "closed"]]), "status": "accepted"}])[0]
    assert not S.score_pi5([inv])[0]
    assert S.score_pi5([machine([["open", "closed"]])])[1]["in_predicted_range"] is False


GOT = {"tables": 4, "users_table": "User", "owners": {"Ticket": "reporter_id"},
       "private": {"User": ["password_hash"]}, "admin_routes": [], "machines": {}, "baseline": []}  # fmt: skip


def test_pa1_is_exact_and_the_baseline_equals_a_separate_run():
    assert S.score_pa1(GOT, True)
    assert not S.score_pa1(GOT, False)
    assert not S.score_pa1({**GOT, "baseline": ["GO802|POST /login|User.token"]}, True)
    assert not S.score_pa1(
        {**GOT, "owners": {"Ticket": "reporter_id", "Handoff": "from_user_id"}}, True
    )
    assert not S.score_pa1({**GOT, "users_table": "users"}, True)
    assert not S.score_pa1({**GOT, "tables": 3}, True)
    assert not S.score_pa1({**GOT, "machines": {"Ticket": "status"}}, True)
    assert not S.score_pa1({**GOT, "private": {"User": ["password_hash", "name"]}}, True)


def test_pa3_wants_each_mutants_code_and_key_and_nothing_outside_the_baseline():
    new = {"JM31": [{"code": "GO801", "key": "GO801|POST /tickets/:tid/close|Ticket"}],
           "JM32": [{"code": "GO802", "key": "GO802|GET /me|User.password_hash"}]}  # fmt: skip
    assert S.score_pa3([], new) == (True, {"JM31": True, "JM32": True})
    assert S.score_pa3(["GO801|GET /tickets|Ticket"], new)[0] is False
    bad = {**new, "JM32": [{"code": "GO802", "key": "GO802|POST /login|User.password_hash"}]}
    assert S.score_pa3([], bad) == (False, {"JM31": True, "JM32": False})
    bad = {**new, "JM31": [{"code": "GO801", "key": "GO801|POST /tickets/:tid/reopen|Ticket"}]}
    assert S.score_pa3([], bad)[1]["JM31"] is False
    assert S.score_pa3([], {})[0] is False


def test_the_sub_claims_of_arm_0_and_px():
    pairs = list(S.T_S3_PARSED)
    assert S.score_p0_parse(pairs, pairs, 5)
    assert not S.score_p0_parse(pairs[:4], pairs, 5)
    assert not S.score_p0_parse(pairs, pairs, 4)
    assert not S.score_p0_parse(pairs, [*pairs, ("GET /queues", "x")], 5)
    assert S.score_p0_omit((0, 100)) and not S.score_p0_omit((1, 100))
    assert not S.score_p0_omit((0, 50))
    assert S.score_p0_bind(422, [{"status": 422}]) and not S.score_p0_bind(400, [{"status": 400}])
    assert not S.score_p0_bind(None, [{"unclear": True, "statuses": [400, 422]}])
    assert S.score_p0_choices({"Ticket.status": ("open", "closed")})
    assert not S.score_p0_choices({"Ticket.status": ["closed", "open"]})
    assert not S.score_p0_choices({"Ticket.status": ["open", "closed"], "Queue.name": ["a"]})
    assert S.score_p0_priv({"User": ["password_hash"]})
    assert not S.score_p0_priv({"User": ["password_hash"], "Queue": ["x"]})
    assert S.score_px(179.9) and not S.score_px(180.1) and not S.score_px(None)


def test_ids_are_the_ten_rows_and_the_seven_sub_claims():
    assert S.ROW_IDS == ["PI1-3", "PI2-3", "PI3-3", "PI4-3", "PI5-3", "PI6-3", "PI7-3", "PR3-3",
                         "PA1-3", "PA3-3"]  # fmt: skip
    assert len(S.SUB_IDS) == 7 and len(S.ALL_IDS) == 17
    assert set(S.IMPORT_ROWS) <= set(S.ALL_IDS)


# ---- Go source edits (synthetic source) ----------------------------------------------------------

API = """package api

import "net/http"

func Register(r *gin.Engine, db *gorm.DB) {
	auth := r.Group("", RequireUser(db))
	tickets := auth.Group("/tickets")
	tickets.GET("/:tid", func(c *gin.Context) {
		tid, err := strconv.Atoi(c.Param("tid"))
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"detail": "bad id {"})
			return
		}
		var t models.Ticket
		if err := db.Where("id = ? AND reporter_id = ?", tid, c.GetUint("uid")).First(&t).Error; err != nil {
			c.JSON(http.StatusNotFound, gin.H{"detail": "Not found"})
			return
		}
		c.JSON(http.StatusOK, t)
	})
	tickets.POST("/:tid/close", closeTicket(db))
	tickets.POST("/:tid/reopen", s.reopen)
	r.POST("/queues/:qid/tickets", RequireUser(db), createTicket(db))
}

func closeTicket(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var t models.Ticket
		if err := db.Where("id = ? AND reporter_id = ?", c.Param("tid"), c.GetUint("uid")).First(&t).Error; err != nil {
			c.JSON(http.StatusNotFound, gin.H{"detail": "Not found"})
			return
		}
		if t.Status != "open" {
			c.JSON(http.StatusConflict, gin.H{"detail": "not open"})
			return
		}
		db.Model(&t).Update("status", "closed")
		c.JSON(http.StatusOK, t)
	}
}

func (s *server) reopen(c *gin.Context) {
	var t models.Ticket
	if err := s.db.Where("reporter_id = ?", c.GetUint("uid")).Where("id = ?", c.Param("tid")).First(&t).Error; err != nil {
		c.JSON(404, gin.H{"detail": "Not found"})
		return
	}
	if t.Status != "closed" {
		c.JSON(409, gin.H{"detail": "not closed"})
		return
	}
	s.db.Model(&t).Update("status", "open")
}

func createTicket(db *gorm.DB) gin.HandlerFunc { return func(c *gin.Context) {} }
"""
MODELS = """package models

type User struct {
	ID           uint   `gorm:"primaryKey" json:"id"`
	Name         string `gorm:"size:64;uniqueIndex" json:"name"`
	PasswordHash string `gorm:"size:128;not null" json:"-"`
}

type Queue struct {
	ID   uint   `json:"id"`
	Name string `json:"-" gorm:"size:8"`
}
"""


@pytest.fixture
def module(tmp_path):
    root = tmp_path / "gotickets"
    (root / "api").mkdir(parents=True)
    (root / "models").mkdir()
    (root / "api/api.go").write_text(API)
    (root / "models/models.go").write_text(MODELS)
    return root


def test_the_handler_is_found_through_groups_names_calls_and_methods(module):
    src = S.load_sources(module)
    for method, path, needle in [
        ("GET", "/tickets/:tid", "Atoi"),
        ("POST", "/tickets/:tid/close", 'Update("status", "closed")'),
        ("POST", "/tickets/:tid/reopen", 'Update("status", "open")'),
    ]:
        f, lb, rb = S.handler_body(src, method, path)
        assert f == "api/api.go" and src[f][lb] == "{" and src[f][rb] == "}"
        assert needle in src[f][lb : rb + 1]
    f, lb, rb = S.handler_body(src, "GET", "/tickets/:tid")
    assert "closeTicket" not in src[f][lb : rb + 1]  # the inline literal only
    f, lb, rb = S.handler_body(src, "POST", "/queues/:qid/tickets")  # last arg, no group
    assert src[f][lb : rb + 1] == "{ return func(c *gin.Context) {} }"
    with pytest.raises(LookupError):
        S.handler_body(src, "GET", "/nothing")


def test_jm31_drops_the_owner_clause_and_its_argument_only_in_the_close_handler(module):
    changed = S.apply_mutant(module, "JM31")
    assert changed == ["api/api.go"]
    new = (module / "api/api.go").read_text()
    assert 'db.Where("id = ?", c.Param("tid")).First(&t)' in new
    assert new.count("reporter_id") == API.count("reporter_id") - 1  # one clause, in close
    assert 'db.Where("id = ? AND reporter_id = ?", tid, c.GetUint("uid"))' in new  # get is intact


def test_the_owner_clause_forms():
    c = S.drop_owner_clause
    assert (
        c('x.Where("id = ? AND reporter_id = ?", a, f(b, c))', "reporter_id")
        == 'x.Where("id = ?", a)'
    )
    assert c('x.Where("reporter_id = ? AND id = ?", u, a)', "reporter_id") == 'x.Where("id = ?", a)'
    assert c('x.Where("a = ? AND t.reporter_id = ? AND b = ?", p, u, q)', "reporter_id") == (
        'x.Where("a = ? AND b = ?", p, q)'
    )
    assert c('x.Where("id = ?", a).Where("reporter_id = ?", u).First(&t)', "reporter_id") == (
        'x.Where("id = ?", a).First(&t)'
    )
    with pytest.raises(ValueError):
        c('x.Where("id = ?", a)', "reporter_id")


def test_jm33_answers_403_for_the_missing_ticket_and_only_there(module):
    S.apply_mutant(module, "JM33")
    new = (module / "api/api.go").read_text()
    get = new[new.index("tickets.GET") : new.index('tickets.POST("/:tid/close"')]
    assert "http.StatusForbidden" in get and "http.StatusNotFound" not in get
    assert "http.StatusBadRequest" in get
    assert new.count("http.StatusNotFound") == API.count("http.StatusNotFound") - 1


def test_jm34_makes_the_409_check_unreachable_in_reopen(module):
    S.apply_mutant(module, "JM34")
    new = (module / "api/api.go").read_text()
    assert 'if false && (t.Status != "closed") {' in new
    assert 'if t.Status != "open" {' in new  # close still refuses
    assert new.count("if false &&") == 1


def test_skip_conflict_check_keeps_an_init_statement():
    body = '{\n\tif x := f(); x.Status != "closed" {\n\t\tc.JSON(409, nil)\n\t}\n}'
    assert S.skip_conflict_check(body) == (
        '{\n\tif x := f(); false && (x.Status != "closed") {\n\t\tc.JSON(409, nil)\n\t}\n}'
    )
    with pytest.raises(ValueError):
        S.skip_conflict_check("{ return }")


def test_jm32_loses_json_dash_on_password_hash_only(module):
    changed = S.apply_mutant(module, "JM32")
    assert changed == ["models/models.go"]
    new = (module / "models/models.go").read_text()
    assert 'PasswordHash string `gorm:"size:128;not null"`' in new
    assert 'Name         string `gorm:"size:64;uniqueIndex" json:"name"`' in new
    assert 'json:"-" gorm:"size:8"' in new  # another field's dash is untouched


def test_a_tag_that_is_only_the_dash_is_removed_whole():
    out = S.drop_json_dash(
        'type U struct {\n\tPasswordHash string `json:"-"`\n}\n', "PasswordHash", "password_hash"
    )
    assert out == "type U struct {\n\tPasswordHash string\n}\n"
    with pytest.raises(ValueError):
        S.drop_json_dash(
            'type U struct {\n\tName string `json:"name"`\n}\n', "PasswordHash", "password_hash"
        )


def test_a_mutant_that_does_not_apply_raises_instead_of_guessing(module):
    (module / "api/api.go").write_text(API.replace('"/:tid/close"', '"/:tid/finish"'))
    with pytest.raises(LookupError):
        S.apply_mutant(module, "JM31")


def test_literal_edits_override_and_must_match_once(module):
    S.EDIT_OVERRIDE["JM31"] = [{"file": "api/api.go", "old": "not open", "new": "is closed"}]
    try:
        S.apply_mutant(module, "JM31")
        assert "is closed" in (module / "api/api.go").read_text()
        S.EDIT_OVERRIDE["JM31"] = [{"file": "api/api.go", "old": "Not found", "new": "x"}]
        with pytest.raises(ValueError):
            S.apply_mutant(module, "JM31")
    finally:
        S.EDIT_OVERRIDE.clear()


def test_the_structural_edit_reproduces_round_one_s_hand_written_jm5_on_goginapp(tmp_path):
    """The same locate-and-drop code, on a route the author of S2 wrote: it makes JM5's edit."""
    root = tmp_path / "goginapp"
    shutil.copytree(P1.GOGINAPP, root)
    src = S.load_sources(root)
    f, lb, rb = S.handler_body(src, "POST", "/runs/:rid/cancel")
    body = src[f][lb : rb + 1]
    new = S.drop_owner_clause(body, "user_id")
    rel, old, repl = P1.MUT_EDITS["JM5"]
    assert f == rel
    assert src[f][:lb] + new + src[f][rb + 1 :] == src[f].replace(old, repl)


def test_jm6_style_edit_on_goginapp_matches_round_ones(tmp_path):
    root = tmp_path / "goginapp"
    shutil.copytree(P1.GOGINAPP, root)
    src = (root / "models/models.go").read_text()
    new = S.drop_json_dash(src, "Token", "token")
    rel, old, repl = P1.MUT_EDITS["JM6"]
    assert new == src.replace(old, repl)


# ---- the mutants' judges -------------------------------------------------------------------------


def test_the_r_judge_needs_verified_then_refuted():
    item = "R3 GET /tickets/{tid} 404 Not found"
    other = "R3 POST /tickets/{tid}/close 404 Not found"
    c = {item: "verified", other: "verified"}
    assert M.judge_r("JM33", c, {item: "refuted", other: "verified"})["killed"]
    assert not M.judge_r("JM33", c, {item: "verified", other: "refuted"})["killed"]
    assert not M.judge_r("JM33", {item: "unexercised"}, {item: "refuted"})["killed"]
    reopen = "R3 POST /tickets/{tid}/reopen 409 not closed"
    assert M.judge_r("JM34", {reopen: "verified (corrected)"}, {reopen: "refuted"})["killed"]
    assert not M.judge_r("JM34", {reopen: "verified"}, {})["killed"]
    assert M.judge_r("JM31", {"R2 POST /tickets/{tid}/close": "verified"},
                     {"R2 POST /tickets/{tid}/close": "refuted"})["killed"]  # fmt: skip
    assert M.judge_r("JM32", {"T2 User.password_hash": "verified"},
                     {"T2 User.password_hash": "refuted"})["killed"]  # fmt: skip


def test_the_layer_a_judge():
    new = [{"code": "GO801", "key": "GO801|POST /tickets/:tid/close|Ticket"}]
    assert M.judge_a("JM31", new, [])["killed"]
    assert not M.judge_a("JM31", new, ["GO801|GET /tickets|Ticket"])["killed"]
    assert not M.judge_a("JM32", new, [])["killed"]
    assert set(S.MUTANTS) == {"JM31", "JM32", "JM33", "JM34"} == set(M.R_ITEM)


# ---- the self-hash checks ------------------------------------------------------------------------


def listing(tmp_path, paths, tamper=None):
    lines = []
    for rel in paths:
        digest = S.sha(REPO / rel)
        lines.append(f"{('0' * 64) if rel == tamper else digest}  {rel}")
    f = tmp_path / "freeze.sha256"
    f.write_text("\n".join(lines) + "\n")
    return f


THIS = REPO / "dogfood/go_parity_s3.py"


def test_the_self_check_passes_for_a_true_listing(tmp_path):
    f = listing(tmp_path, S.OWN_FILES)
    got = S.verify_listed(f, must_list=S.OWN_FILES, this=THIS)
    assert got["mismatches"] == 0 and got["entries"] == 3


@pytest.mark.parametrize("what", ["tamper", "missing_entry", "script_unlisted", "no_file"])
def test_the_self_check_aborts(tmp_path, what):
    if what == "no_file":
        f = tmp_path / "absent.sha256"
    elif what == "tamper":
        f = listing(tmp_path, S.OWN_FILES, tamper="tests/spec/test_go_parity_s3.py")
    elif what == "missing_entry":
        f = listing(tmp_path, S.OWN_FILES[:2])
    else:
        f = listing(
            tmp_path, ["dogfood/go_parity_s3_mutants.py", "tests/spec/test_go_parity_s3.py"]
        )
    with pytest.raises(SystemExit):
        S.verify_listed(f, must_list=S.OWN_FILES, this=THIS)


def test_main_verifies_both_freezes_first_and_has_no_way_to_skip_them(tmp_path, monkeypatch):
    bad = listing(tmp_path, S.OWN_FILES, tamper="dogfood/go_parity_s3.py")
    monkeypatch.setattr(S, "S3_FREEZE", bad)
    for run in (S.main, M.main):
        argv = [
            "--base",
            str(tmp_path),
            "--scratch",
            str(tmp_path / "s"),
            "--out",
            str(tmp_path / "o"),
        ]
        with pytest.raises(SystemExit) as e:
            run(argv)
        assert "freeze check failed" in str(e.value)
    monkeypatch.setattr(S, "AMENDMENT3_FREEZE", bad)
    with pytest.raises(SystemExit) as e:
        S.main(["--base", str(tmp_path), "--out", str(tmp_path / "o")])
    assert "freeze check failed" in str(e.value)
    assert not (tmp_path / "o").exists()  # nothing was computed or written
    for mod in (S, M):
        text = Path(mod.__file__).read_text()
        for flag in ("--freeze", "--no-freeze", "--skip", "--no-verify", "--dev"):
            assert f'add_argument("{flag}"' not in text


def test_the_fixture_override_marks_the_output_as_a_stand_in():
    assert not S.Fx().stand_in
    assert S.Fx("goginapp", "tests.spec.gogin_harness").stand_in
    assert (
        S.Fx("gotickets", "tests.spec.gotickets_harness").module_dir
        == REPO / "tests/spec/gotickets"
    )


# ---- the static rows run on any Gin module (goginapp is the stand-in) -----------------------------

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")


@needs_go
def test_the_static_sub_claims_run_on_the_stand_in_and_do_not_pass_t_s3():
    ctx = S.Ctx(Path("/nonexistent"), Path("/nonexistent"), "193f7f6")
    ctx.fx = S.Fx("goginapp", "tests.spec.gogin_harness")
    rows = {r["id"]: r for r in (S.sub_parse(ctx), S.sub_omit(ctx), S.sub_bind(ctx),
                                 S.sub_choices(ctx), S.sub_priv(ctx))}  # fmt: skip
    assert rows["P0-BIND:S3"]["observed"]["validation_status"] == 422
    assert rows["P0-BIND:S3"]["pass"] is True  # S2 also refuses every bind error with 422
    assert rows["P0-CHOICES:S3"]["observed"] == {"Run.status": ["queued", "cancelled"]}
    assert rows["P0-CHOICES:S3"]["pass"] is False
    assert rows["P0-PRIV:S3"]["observed"] == {"User": ["password", "token"]}
    assert rows["P0-PRIV:S3"]["pass"] is False
    assert (
        rows["P0-PARSE:S3"]["pass"] is False and rows["P0-OMIT:S3"]["observed"]["page_size"] is None
    )
    json.dumps(list(rows.values()), default=str)
