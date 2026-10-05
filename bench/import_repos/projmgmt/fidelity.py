"""Fidelity scenarios for the vendored project-management-api (``bench/import_repos/projmgmt``).

Written from the upstream tests (``tests/test_{auth,users,projects,tasks,middleware}.py``;
not vendored), the README and the route docstrings, then recorded against the running app
on ``seed.py``'s data: every expected status and body fragment below is what the app
answers. Users are ``seed.SEED_USERS`` handles (logged in through ``POST /auth/login``).
Ids follow ``seed.py``'s insertion order. ``expect_body`` is a subset match
(``lattice.spec.check.subset``).

Not used by the importer; ``bench/heldout/test_heldout_apps.py`` replays them.
"""

from __future__ import annotations

from lattice.spec.model import Scenario, Step

NO_HEADER = {"error": "Authorization header is required"}
NOT_MANAGER = {"error": "Access denied. Manager role required.", "user_type": "employee"}

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "the root describes the API",
        steps=(
            Step("GET", "/", expect_body={"message": "Project Management API",
                                          "endpoints": {"users": "/users"}}),  # fmt: skip
            Step("GET", "/nowhere", expect_status=404, expect_body={"error": "Resource not found"}),
        ),
    ),
    Scenario(
        "login answers tokens and the user for valid credentials",
        steps=(
            Step(
                "POST",
                "/auth/login",
                json={"email": "alice@example.com", "password": "alicepass1"},
                expect_body={
                    "message": "Login successful",
                    "user": {"id": 2, "email": "alice@example.com", "user_type": "employee"},
                },
            ),
        ),
    ),
    Scenario(
        "login refuses unknown emails, wrong passwords and bad payloads",
        steps=(
            Step("POST", "/auth/login", json={"email": "nobody@example.com", "password": "x12345"},
                 expect_status=401, expect_body={"error": "Invalid email or password"}),  # fmt: skip
            Step("POST", "/auth/login", json={"email": "alice@example.com", "password": "wrong1"},
                 expect_status=401, expect_body={"error": "Invalid email or password"}),  # fmt: skip
            Step("POST", "/auth/login", json={"email": "alice@example.com"}, expect_status=400,
                 expect_body={"error": "Missing required fields: password"}),  # fmt: skip
            Step("POST", "/auth/login", json={"email": "not-an-email", "password": "x12345"},
                 expect_status=400, expect_body={"error": "Invalid email format"}),  # fmt: skip
            Step("POST", "/auth/login", json={"email": "alice@example.com", "password": "123"},
                 expect_status=400),  # fmt: skip
        ),
    ),
    Scenario(
        "register creates an employee by default and refuses duplicates",
        steps=(
            Step(
                "POST",
                "/auth/register",
                json={"name": "Nina New", "email": "nina@example.com", "password": "ninapass1"},
                expect_status=201,
                expect_body={"message": "User registered successfully",
                             "user": {"id": 4, "name": "Nina New", "user_type": "employee"}},  # fmt: skip
            ),
            Step("POST", "/auth/login", json={"email": "nina@example.com", "password": "ninapass1"}),
            Step(
                "POST",
                "/auth/register",
                json={"name": "Again", "email": "alice@example.com", "password": "alicepass1"},
                expect_status=409,
                expect_body={"error": "Email already exists"},
            ),
            Step("POST", "/auth/register", json={"name": "X", "email": "x@example.com"},
                 expect_status=400, expect_body={"error": "Missing required fields: password"}),  # fmt: skip
            Step("POST", "/auth/register",
                 json={"name": "X", "email": "x@example.com", "password": "xpass12",
                       "user_type": "boss"}, expect_status=400),  # fmt: skip
            Step("POST", "/auth/register",
                 json={"name": "Max", "email": "max@example.com", "password": "maxpass1",
                       "user_type": "manager"}, expect_status=201,
                 expect_body={"user": {"user_type": "manager"}}),  # fmt: skip
        ),
    ),
    Scenario(
        "refresh needs a valid refresh token",
        steps=(
            Step("POST", "/auth/refresh", json={}, expect_status=400,
                 expect_body={"error": "Refresh token is required"}),  # fmt: skip
            Step("POST", "/auth/refresh", json={"refresh_token": "not-a-token"}, expect_status=401,
                 expect_body={"error": "Invalid or expired refresh token"}),  # fmt: skip
        ),
    ),
    Scenario(
        "logout and verify need a token",
        steps=(
            Step("POST", "/auth/logout", expect_status=401, expect_body=NO_HEADER),
            Step("POST", "/auth/logout", user="alice", expect_body={"message": "Logout successful"}),
            Step("GET", "/auth/verify", expect_status=401,
                 expect_body={"error": "Authorization header required"}),  # fmt: skip
            Step("GET", "/auth/verify", user="role_manager",
                 expect_body={"valid": True, "user": {"id": 1, "user_type": "manager"}}),  # fmt: skip
        ),
    ),
    Scenario(
        "listing users needs a token, and filters and pages",
        steps=(
            Step("GET", "/users", expect_status=401, expect_body=NO_HEADER),
            Step("GET", "/users", user="alice", expect_body={"count": 3}),
            Step("GET", "/users", user="alice", params={"user_type": "manager"},
                 expect_body={"count": 1, "users": [{"id": 1, "name": "Maya Manager"}]}),  # fmt: skip
            Step("GET", "/users", user="alice", params={"user_type": "boss"}, expect_status=400),
            Step("GET", "/users", user="alice", params={"limit": 1, "offset": 1},
                 expect_body={"count": 1, "users": [{"id": 2}]}),  # fmt: skip
        ),
    ),
    Scenario(
        "a user's detail, with their projects on request",
        steps=(
            Step("GET", "/users/2", expect_status=401),
            Step("GET", "/users/2", user="bob",
                 expect_body={"id": 2, "email": "alice@example.com"}),  # fmt: skip
            Step("GET", "/users/2", user="bob", params={"include_projects": "true"},
                 expect_body={"projects": [{"id": 1, "name": "Website redesign"},
                                           {"id": 2, "name": "Mobile app"}]}),  # fmt: skip
            Step("GET", "/users/999", user="bob", expect_status=404,
                 expect_body={"error": "User not found"}),  # fmt: skip
        ),
    ),
    Scenario(
        "only managers create users",
        steps=(
            Step("POST", "/users", json={"name": "E", "email": "e@example.com",
                                         "user_type": "employee"}, expect_status=401),  # fmt: skip
            Step("POST", "/users", user="alice",
                 json={"name": "E", "email": "e@example.com", "user_type": "employee"},
                 expect_status=403, expect_body=NOT_MANAGER),  # fmt: skip
            Step("POST", "/users", user="role_manager",
                 json={"name": "Eve Employee", "email": "eve@example.com", "user_type": "employee"},
                 expect_status=201,
                 expect_body={"id": 4, "name": "Eve Employee", "user_type": "employee"}),  # fmt: skip
            Step("POST", "/auth/login",
                 json={"email": "eve@example.com", "password": "defaultpassword123"}),  # fmt: skip
            Step("POST", "/users", user="role_manager",
                 json={"name": "Dup", "email": "bob@example.com", "user_type": "employee"},
                 expect_status=409, expect_body={"error": "Email already exists"}),  # fmt: skip
            Step("POST", "/users", user="role_manager", json={"name": "No email"},
                 expect_status=400, expect_body={"error": "Field 'email' is required"}),  # fmt: skip
        ),
    ),
    Scenario(
        "only managers update users",
        steps=(
            Step("PUT", "/users/2", user="alice", json={"name": "Me"}, expect_status=403),
            Step("PUT", "/users/2", user="role_manager", json={"name": "Alice A."},
                 expect_body={"id": 2, "name": "Alice A.", "email": "alice@example.com"}),  # fmt: skip
            Step("PUT", "/users/2", user="role_manager", json={"email": "bad"}, expect_status=400,
                 expect_body={"error": "Invalid email format"}),  # fmt: skip
            Step("PUT", "/users/2", user="role_manager", json={"email": "bob@example.com"},
                 expect_status=409),  # fmt: skip
            Step("PUT", "/users/2", user="role_manager", json={"user_type": "manager"},
                 expect_body={"user_type": "manager"}),  # fmt: skip
            Step("PUT", "/users/999", user="role_manager", json={"name": "X"}, expect_status=404),
        ),
    ),
    Scenario(
        "only managers delete users; their projects and tasks go too",
        steps=(
            Step("DELETE", "/users/3", user="bob", expect_status=403),
            Step("DELETE", "/users/3", user="role_manager",
                 expect_body={"message": "User deleted successfully"}),  # fmt: skip
            Step("GET", "/projects", params={"user_id": 3}, expect_body={"count": 0}),
            Step("GET", "/tasks", user="role_manager", expect_body={"count": 3}),
            Step("DELETE", "/users/999", user="role_manager", expect_status=404),
        ),
    ),
    Scenario(
        "anyone reads projects, with filters and tasks on request",
        steps=(
            Step("GET", "/projects", expect_body={"count": 3}),
            Step("GET", "/projects", params={"user_id": 2},
                 expect_body={"count": 2, "projects": [{"user_id": 2}, {"user_id": 2}]}),  # fmt: skip
            Step("GET", "/projects", params={"limit": 1, "include_tasks": "true"},
                 expect_body={"count": 1, "projects": [{"id": 1, "tasks": [{"id": 1}, {"id": 2}]}]}),  # fmt: skip
            Step("GET", "/projects/3", expect_body={"id": 3, "name": "Data pipeline",
                                                    "user_id": 3}),  # fmt: skip
            Step("GET", "/projects/999", expect_status=404, expect_body={"error": "Project not found"}),
        ),
    ),
    Scenario(
        "only managers create projects, for an existing user",
        steps=(
            Step("POST", "/projects", json={"name": "P", "user_id": 2}, expect_status=401),
            Step("POST", "/projects", user="alice", json={"name": "P", "user_id": 2},
                 expect_status=403, expect_body=NOT_MANAGER),  # fmt: skip
            Step("POST", "/projects", user="role_manager",
                 json={"name": "Analytics", "description": "Dashboards", "user_id": 3},
                 expect_status=201,
                 expect_body={"id": 4, "name": "Analytics", "user_id": 3}),  # fmt: skip
            Step("POST", "/projects", user="role_manager", json={"user_id": 3}, expect_status=400,
                 expect_body={"error": "Field 'name' is required"}),  # fmt: skip
            Step("POST", "/projects", user="role_manager", json={"name": "P", "user_id": 999},
                 expect_status=404, expect_body={"error": "User not found"}),  # fmt: skip
            Step("POST", "/projects", user="role_manager", json={"name": "P", "user_id": 0},
                 expect_status=400),  # fmt: skip
        ),
    ),
    Scenario(
        "only managers update and delete projects",
        steps=(
            Step("PUT", "/projects/1", user="alice", json={"name": "Mine"}, expect_status=403),
            Step("PUT", "/projects/1", user="role_manager",
                 json={"name": "Website v2", "description": None},
                 expect_body={"id": 1, "name": "Website v2", "description": None,
                              "user_id": 2}),  # fmt: skip
            Step("PUT", "/projects/1", user="role_manager", json={"name": ""}, expect_status=400),
            Step("DELETE", "/projects/2", user="alice", expect_status=403),
            Step("DELETE", "/projects/2", user="role_manager",
                 expect_body={"message": "Project deleted successfully"}),  # fmt: skip
            Step("GET", "/projects/2", expect_status=404),
            Step("GET", "/tasks/3", expect_status=404),
            Step("DELETE", "/projects/999", user="role_manager", expect_status=404),
        ),
    ),
    Scenario(
        "listing all tasks needs a token, and filters",
        steps=(
            Step("GET", "/tasks", expect_status=401, expect_body=NO_HEADER),
            Step("GET", "/tasks", user="alice", expect_body={"count": 4}),
            Step("GET", "/tasks", user="alice", params={"status": "pending"},
                 expect_body={"count": 2, "tasks": [{"id": 1}, {"id": 4}]}),  # fmt: skip
            Step("GET", "/tasks", user="alice", params={"status": "bogus"}, expect_status=400),
            Step("GET", "/tasks", user="alice", params={"project_id": 1, "limit": 1},
                 expect_body={"count": 1, "tasks": [{"id": 1, "project_id": 1}]}),  # fmt: skip
        ),
    ),
    Scenario(
        "any logged-in user creates a task in any project",
        steps=(
            Step("POST", "/tasks", json={"title": "T", "project_id": 3}, expect_status=401),
            Step("POST", "/tasks", user="alice", json={"title": "Review schema", "project_id": 3},
                 expect_status=201,
                 expect_body={"id": 5, "title": "Review schema", "status": "pending",
                              "project_id": 3}),  # fmt: skip
            Step("POST", "/tasks", user="alice", json={"title": "No project"}, expect_status=400,
                 expect_body={"error": "project_id is required"}),  # fmt: skip
            Step("POST", "/tasks", user="alice",
                 json={"title": "T", "project_id": 3, "status": "done"}, expect_status=400),  # fmt: skip
            Step("POST", "/tasks", user="alice", json={"title": "T", "project_id": 999},
                 expect_status=404, expect_body={"error": "Project not found"}),  # fmt: skip
            Step("POST", "/tasks", user="alice", json={"project_id": 3}, expect_status=400,
                 expect_body={"error": "Field 'title' is required"}),  # fmt: skip
        ),
    ),
    Scenario(
        "only managers add tasks through a project's route",
        steps=(
            Step("POST", "/projects/1/tasks", user="alice", json={"title": "T"}, expect_status=403),
            Step("POST", "/projects/1/tasks", user="role_manager",
                 json={"title": "Launch", "status": "in_progress"}, expect_status=201,
                 expect_body={"title": "Launch", "status": "in_progress", "project_id": 1}),  # fmt: skip
            Step("POST", "/projects/999/tasks", user="role_manager", json={"title": "T"},
                 expect_status=404),  # fmt: skip
            Step("POST", "/projects/1/tasks", user="role_manager", json={"title": "   "},
                 expect_status=400),  # fmt: skip
        ),
    ),
    Scenario(
        "anyone reads a project's tasks and a task",
        steps=(
            Step("GET", "/projects/1/tasks",
                 expect_body={"count": 2, "project_id": 1,
                              "tasks": [{"title": "Design homepage"}, {"title": "Write copy"}]}),  # fmt: skip
            Step("GET", "/projects/1/tasks", params={"status": "in_progress"},
                 expect_body={"count": 1}),  # fmt: skip
            Step("GET", "/projects/1/tasks", params={"status": "x"}, expect_status=400),
            Step("GET", "/projects/999/tasks", expect_status=404),
            Step("GET", "/tasks/3", expect_body={"id": 3, "status": "completed", "project_id": 2}),
            Step("GET", "/tasks/999", expect_status=404, expect_body={"error": "Task not found"}),
        ),
    ),
    Scenario(
        "any logged-in user updates or deletes any task",
        steps=(
            Step("PUT", "/tasks/4", json={"status": "completed"}, expect_status=401),
            Step("PUT", "/tasks/4", user="alice", json={"status": "completed"},
                 expect_body={"id": 4, "status": "completed", "project_id": 3}),  # fmt: skip
            Step("PUT", "/tasks/4", user="alice", json={"status": "archived"}, expect_status=400),
            Step("PUT", "/tasks/999", user="alice", json={"status": "pending"}, expect_status=404),
            Step("DELETE", "/tasks/4", expect_status=401),
            Step("DELETE", "/tasks/4", user="alice",
                 expect_body={"message": "Task deleted successfully"}),  # fmt: skip
            Step("DELETE", "/tasks/4", user="alice", expect_status=404),
        ),
    ),
)
