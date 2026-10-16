# ci-bot requirements

- ci-bot logs in with POST /auth/login using email and password and keeps the access_token.
- It finds the application named alpha through GET /applications.
- It starts the newest version of the job with `POST /applications/{app}/jobs/{job}/runs` and expects 201 with id and status.
- On a timeout it lists the job's runs (GET /applications/{app}/jobs/{job}/runs) and cancels its own run with POST /applications/{app}/runs/{run}/cancel, reading status.
- It polls /applications/{app}/runs/{run} every 5 seconds until a final status.
- The bot never changes a job.
