"""A small spec and trace for the Gin fixture app in ``app/`` (tests of the implementation diff).

What the fixture is built to show (the app is ``app/``; its extract and workflows are the
JSON files beside this one):

- R1 login, R2 create a project, R3 publish (plus the Project.status machine), R10 activate a
  job (plus the Job.status machine), R9 ``GET /health``: implemented;
- R4 names a route the app lacks (text only), R5 restores a project (a setup step the app has
  and a focus step it lacks): required_not_implemented, the second partial;
- R6 is context (no element), R7 and R8 link only tables (always unverified);
- ``GET /api/stats`` is in the app and in the scenario ``stats`` but no rule links it (an
  orphan); ``PUT /test/clock`` is registered only under a test flag (excluded, test_only).
"""

from __future__ import annotations

from lattice.profile import ApiProfile, NoRoles, Profile
from lattice.spec import Invariant, Machine, Scenario, Spec, Step
from lattice.spec.rules import Rule, Trace
from lattice.typesys import Scalar, Schema

S = Scalar
USER = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("token", S.STR)],
    keys=[("id",), ("email",), ("token",)],
    private=["token"],
)
PROJECT = Schema.from_pairs(
    [("id", S.STR), ("owner_id", S.INT), ("name", S.STR), ("kind", S.STR), ("status", S.STR)],
    keys=[("id",)],
    owner="owner_id",
    one_of={"status": ("draft", "published", "archived")},
)
TICKET = Schema.from_pairs(
    [("id", S.INT), ("project_id", S.STR), ("subject", S.STR)], keys=[("id",)]
)
JOB = Schema.from_pairs(
    [("id", S.INT), ("owner_id", S.INT), ("name", S.STR), ("status", S.STR)],
    keys=[("id",)],
    owner="owner_id",
    one_of={"status": ("queued", "active", "done")},
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"User": USER, "Project": PROJECT, "Ticket": TICKET, "Job": JOB},
        table_names={"User": "users", "Project": "projects", "Ticket": "tickets", "Job": "jobs"},
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=NoRoles("Member"),
        error_body="fastapi",
    ),
)
PID = "11111111-1111-4111-8111-111111111111"
NEW = {"id": PID, "name": "p", "kind": "public"}

SPEC = Spec(
    profile=PROFILE,
    users={"ann": ("ann@example.com", "pw"), "ben": ("ben@example.com", "pw")},
    invariants=(
        Invariant(
            "tickets_bounded",
            "A project has at most 50 tickets.",
            'count(Ticket, by="project_id") <= 50',
        ),
        Invariant(
            "kind_is_known",
            "A project is public or private.",
            'Project.kind in ("public", "private")',
        ),
        Invariant("garbled", "This one does not parse.", "Project.kind in ("),
    ),
    machines=(
        Machine(
            "Project",
            "status",
            (("draft", "published"), ("draft", "archived"), ("published", "archived")),
            ("draft",),
        ),
        Machine("Job", "status", (("queued", "active"), ("active", "done")), ("queued",)),
    ),
    scenarios=(
        Scenario(
            "login",
            steps=(
                Step(
                    "POST", "/api/login", None, json={"email": "ann@example.com"}, expect_status=200
                ),
            ),
        ),
        Scenario(
            "create_project",
            steps=(Step("POST", "/api/projects", "ann", json=NEW, expect_status=201),),
        ),
        Scenario(
            "publish_project",
            steps=(
                Step("POST", "/api/projects", "ann", json=NEW, expect_status=201),
                Step("POST", f"/api/projects/{PID}/publish", "ann", expect_status=200),
            ),
        ),
        Scenario(
            "restore_project",
            steps=(
                Step("POST", "/api/projects", "ann", json=NEW, expect_status=201),
                Step("POST", f"/api/projects/{PID}/restore", "ann", expect_status=200),
            ),
        ),
        Scenario(
            "activate_job",
            steps=(
                Step("POST", "/api/jobs", "ann", json={"name": "j"}, expect_status=201),
                Step("POST", "/api/jobs/1/activate", "ann", expect_status=204),
            ),
        ),
        Scenario(
            "clock_then_create",
            steps=(
                Step(
                    "PUT",
                    "/test/clock",
                    None,
                    json={"now": "2026-01-01T00:00:00Z"},
                    expect_status=204,
                ),
                Step("POST", "/api/projects", "ann", json=NEW, expect_status=201),
            ),
        ),
        Scenario("health", steps=(Step("GET", "/health", None, expect_status=200),)),
        Scenario("stats", steps=(Step("GET", "/api/stats", "ann", expect_status=200),)),
    ),
)

RULES = [
    Rule("R1", "A user signs in with POST /api/login.", "Fixture", 1),
    Rule("R2", "A user creates a project; it answers 201.", "Fixture", 2),
    Rule("R3", "A draft project can be published; it then is published.", "Fixture", 3),
    Rule("R4", "Tickets can be exported with GET /api/tickets/export.", "Fixture", 4),
    Rule("R5", "A project can be restored after it is archived.", "Fixture", 5),
    Rule("R6", "The service is for small teams.", "Fixture", 6),
    Rule("R7", "A ticket belongs to a project.", "Fixture", 7),
    Rule("R8", "A project has at most 50 tickets.", "Fixture", 8),
    Rule("R9", "The service answers GET /health with 200.", "Fixture", 9),
    Rule("R10", "A queued job can be activated.", "Fixture", 10),
    Rule("R11", "The clock can be set in tests.", "Fixture", 11),
    Rule("R12", "A project's kind is public or private.", "Fixture", 12),
]
LINKS = {
    "R1": ["scenario:login"],
    "R2": ["scenario:create_project"],
    "R3": ["scenario:publish_project", "machine:Project.status"],
    "R4": [],
    "R5": ["scenario:restore_project"],
    "R7": ["profile:Ticket.project_id"],
    "R8": ["invariant:tickets_bounded"],
    "R9": ["scenario:health"],
    "R10": ["scenario:activate_job", "machine:Job.status"],
    "R11": ["scenario:clock_then_create"],
    "R12": ["invariant:kind_is_known", "invariant:garbled"],
}


def trace() -> Trace:
    """The trace. R4 has no link (its status is untraced; its route is in its text, which is
    what makes it required); R6 is context."""
    rules = [Rule(r.id, r.text if r.id != "R4" else r.text, r.section, r.line) for r in RULES]
    return Trace(
        "FIXTURE.md",
        rules,
        links={k: v for k, v in LINKS.items() if v},
        context=["R6"],
        unchecked={"R4": "no scenario exercises the export"},
    )
