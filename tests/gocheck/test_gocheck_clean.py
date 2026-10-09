"""Precision on idiomatic Gin + GORM services -- ``fixtures/clean`` (layered: a store with
owner scopes, a service with transactions and conditional updates, an API mapping errors
to statuses, DTOs, bounded inputs) and ``fixtures/clean2`` (closure handlers over a
package-level DB, an abort helper, the app's own transition table): no finding. And
recall through those layers: each mutation below plants one defect, which must be found.
Skipped when ``go`` is not on PATH."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lattice.gocheck import GoPolicy, all_go_findings

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

CLEAN = Path(__file__).parent / "fixtures" / "clean"
POLICY = GoPolicy(
    tables={"Account": "accounts", "Pipeline": "pipelines", "Run": "pipeline_runs"},
    users_table="Account",
    owners={"Pipeline": "account_id"},
    owner_via={"Run": ("pipeline_id", "Pipeline")},
    private={"Account": ("password_hash",)},
    machines={
        "Run": (
            "status",
            frozenset(
                {
                    ("pending", "active"),
                    ("pending", "cancelled"),
                    ("active", "succeeded"),
                    ("active", "failed"),
                    ("active", "cancelled"),
                }
            ),  # fmt: skip
        )
    },
)

API = "internal/api/api.go"
STORE = "internal/store/store.go"
SERVICE = "internal/service/service.go"
MODELS = "internal/models/models.go"


CLEAN2 = Path(__file__).parent / "fixtures" / "clean2"
POLICY2 = GoPolicy(
    tables={"Ticket": "tickets", "Note": "notes"},
    owners={"Ticket": "reporter_id"},
    owner_via={"Note": ("ticket_id", "Ticket")},
    private={"Ticket": ("secret",)},
    machines={
        "Ticket": (
            "state",
            frozenset(
                {
                    ("open", "assigned"),
                    ("open", "closed"),
                    ("assigned", "resolved"),
                    ("assigned", "open"),
                    ("resolved", "closed"),
                    ("resolved", "assigned"),
                }
            ),
        )
    },  # fmt: skip
)
ROUTES = "routes/routes.go"


@pytest.mark.parametrize(("app", "policy"), [(CLEAN, POLICY), (CLEAN2, POLICY2)])
def test_the_clean_services_have_no_finding(app: Path, policy: GoPolicy):
    found = all_go_findings(app, policy)
    assert found == [], "\n".join(f.render() for f in found)


MUTATIONS = {
    "owner scope dropped": (
        [(STORE, "s.db.Scopes(ownedBy(accountID)).First(&p, id)", "s.db.First(&p, id)")],
        "GO801",
        "GET /v1/pipelines/:pid (Server.getPipeline)",
    ),
    "private column answered": (
        [
            (
                MODELS,
                'PasswordHash string `json:"-"`',
                'PasswordHash string `json:"password_hash"`',
            ),
            (
                API,
                "c.JSON(http.StatusOK, accountOut{ID: acc.ID, Email: acc.Email})",
                "c.JSON(http.StatusOK, acc)",
            ),
        ],
        "GO802",
        "GET /v1/me (Server.me)",
    ),
    "name unbounded": (
        [(API, 'binding:"required,min=1,max=100"', 'binding:"required"')],
        "GO803",
        "POST /v1/pipelines (Server.createPipeline)",
    ),
    "limit unbounded": (
        [(API, "if err != nil || limit < 1 || limit > 100 {", "if err != nil {")],
        "GO803",
        "`.Limit(...)` (in Store.Pipelines)",
    ),
    "a check after the write": (
        [
            (
                API,
                "\tc.JSON(http.StatusCreated, pipelineOut{ID: p.ID, Name: p.Name})",
                '\tif in.Name == "reserved" {\n'
                "\t\tc.JSON(http.StatusConflict, gin.H{})\n\t\treturn\n"
                "\t}\n\tc.JSON(http.StatusCreated, pipelineOut{ID: p.ID, Name: p.Name})",
            )
        ],
        "GO807",
        "Store.CreatePipeline(), which writes s.db.Create",
    ),
    "a write before a service error": (
        [
            (
                SERVICE,
                "\treturn s.store.Transition(",
                "\t_ = s.store.AddRun(&models.Run{PipelineID: pipelineID})\n"
                "\treturn s.store.Transition(",
            )
        ],
        "GO807",
        "Service.Cancel(), which writes",
    ),
    "a write outside the transaction": (
        [(SERVICE, "\t\tst := s.store.WithTx(tx)", "\t\tst := s.store")],
        "GO807",
        "POST /v1/pipelines/:pid/runs (Server.startRun)",
    ),
    "retry rewinds a failed run": (
        [
            (
                SERVICE,
                "\tnext := models.Run{",
                "\told.Status = models.RunPending\n\ts.db.Save(old)\n\tnext := models.Run{",
            )
        ],
        "GO808",
        "'failed' -> 'pending'",
    ),
}


MUTATIONS2 = {
    "a state the workflow forbids": (
        [
            (
                ROUTES,
                "\t\tn := db.Note{",
                '\t\tdb.DB.Model(&t).Update("state", db.Open)\n\t\tn := db.Note{',
            )
        ],
        "GO808",
        "'resolved' -> 'open'",
    ),
    "a note before the check": (
        [
            (ROUTES, "\t\tn := db.Note{TicketID: t.ID, Text: in.Text}\n\t\tdb.DB.Create(&n)\n", ""),
            (
                ROUTES,
                "\t\tif t.State == db.Closed {",
                "\t\tn := db.Note{TicketID: t.ID, Text: in.Text}\n\t\tdb.DB.Create(&n)\n"
                "\t\tif t.State == db.Closed {",
            ),
        ],
        "GO807",
        "abortWith() (it refuses with 409)",
    ),
    "tickets of everyone": (
        [(ROUTES, 'db.DB.Where("reporter_id = ?", uid(c)).Limit(size)', "db.DB.Limit(size)")],
        "GO801",
        "GET /api/tickets (ListTickets.func1)",
    ),
    "the secret answered": (
        [("db/db.go", 'Secret     string `json:"-"`', 'Secret     string `json:"secret"`')],
        "GO802",
        "Ticket.secret",
    ),
    "page size unbounded": (
        [(ROUTES, "n <= 0 || n > 200", "n <= 0")],
        "GO803",
        "`.Limit(...)`",
    ),
}
CASES = [(CLEAN, POLICY, n, MUTATIONS[n]) for n in sorted(MUTATIONS)] + [
    (CLEAN2, POLICY2, n, MUTATIONS2[n]) for n in sorted(MUTATIONS2)
]


@pytest.mark.parametrize(("src", "policy", "name", "mutation"), CASES, ids=[c[2] for c in CASES])
def test_a_planted_defect_is_found(
    src: Path, policy: GoPolicy, name: str, mutation, tmp_path: Path
):
    edits, code, needle = mutation
    app = tmp_path / "app"
    shutil.copytree(src, app)
    for rel, old, new in edits:
        p = app / rel
        text = p.read_text()
        assert old in text, (rel, old)
        p.write_text(text.replace(old, new, 1))
    found = all_go_findings(app, policy)
    assert {f.code for f in found} == {code}, "\n".join(f.render() for f in found)
    assert any(needle in f.message for f in found), "\n".join(f.render() for f in found)
