# Shelf

A stock list of named items served over HTTP and JSON (ASP.NET Core and EF Core over SQLite).

## Items

- Adding an item (`POST /items`, body `{"name", "qty"}`) stores it and answers 201 with `{"id", "name", "qty"}`: a new id, the requested name and quantity.
- Adding an item with an empty name is refused with 422 and stores nothing.
- Adding an item with a negative quantity is refused with 422 and stores nothing.
- Item names are unique: adding an item whose name is already used is refused with 409 and stores nothing.
- Getting an item (`GET /items/{id}`) that does not exist answers 404.
- Listing (`GET /items`) answers 200 `{"items": [...]}` with every stored item, oldest first (by id).
- Removing an item (`DELETE /items/{id}`) deletes it and answers 200; removing an item that does not exist answers 404.
- A stored quantity is never negative.
