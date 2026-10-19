# Shelf

A stock list of named items served over gRPC (`proto/toy/v1/shelf.proto`).

## Items

- Adding an item stores it and answers it with a new id; the stored row has the requested name and quantity.
- Adding an item with an empty name is refused with INVALID_ARGUMENT and stores nothing.
- Adding an item with a negative quantity is refused with INVALID_ARGUMENT and stores nothing.
- Item names are unique: adding an item whose name is already used is refused with ALREADY_EXISTS and stores nothing.
- Getting an item that does not exist answers NOT_FOUND.
- Listing answers every stored item, oldest first (by id).
- Removing an item deletes it; removing an item that does not exist answers NOT_FOUND.
- A stored quantity is never negative.
