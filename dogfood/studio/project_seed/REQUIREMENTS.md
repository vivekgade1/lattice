# Notes app

## Writing notes

- A signed-in user creates a note with POST /notes {"text": ...}; it answers 201 with the note.
- A note's text is 1 to 200 characters; anything else answers 422.

## Reading notes

- GET /notes/{id} answers the caller's own note; another user's note answers 404.
- Requests without a token answer 401.
