# Fixture app

- A user signs in with POST /api/login.
- A user creates a project; it answers 201.
- A draft project can be published; it then is published and the call answers 204.
- Tickets can be exported with GET /api/tickets/export.
- A project can be restored after it is archived.
- The service is for small teams.
- A ticket belongs to a project.
- A project has at most 50 tickets.
- The service answers GET /health with 200.
- A queued job can be activated.
- The clock can be set in tests.
- A project's kind is public or private.
