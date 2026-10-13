"""ci-bot: start the newest version of a job on the job scheduler and wait for its run."""

import os
import sys
import time

import httpx


def main() -> int:
    c = httpx.Client(base_url=os.environ["SCHEDULER_URL"], timeout=10)
    r = c.post(
        "/auth/login",  # I1
        json={"email": os.environ["CI_EMAIL"], "password": os.environ["CI_PASSWORD"]},
    )
    if r.status_code != 200:
        return fail("login", r)
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    app = next(
        a["id"]
        for a in c.get("/applications").json()  # I2
        if a["name"] == os.environ.get("CI_APP", "alpha")
    )
    jobs = [
        j
        for j in c.get(f"/applications/{app}/jobs").json()  # I3
        if j["job_name"] == os.environ.get("CI_JOB", "nightly_report")
    ]
    job = max(jobs, key=lambda j: j["version"])["id"]
    r = c.post(f"/applications/{app}/jobs/{job}/runs")  # I4
    if r.status_code != 201:
        return fail("start", r)
    run, _status = r.json()["id"], r.json()["status"]
    deadline = time.monotonic() + float(os.environ.get("CI_TIMEOUT_S", "600"))
    while time.monotonic() < deadline:
        body = c.get(f"/applications/{app}/runs/{run}").json()  # I5
        if body["status"] in ("succeeded", "failed", "cancelled"):
            print(run, body["status"], body["exit_code"])
            return 0 if body["status"] == "succeeded" else 1
        time.sleep(5)
    runs = c.get(f"/applications/{app}/jobs/{job}/runs").json()  # I6
    print("timeout; runs:", [(x["id"], x["status"]) for x in runs])
    print("cancel:", c.post(f"/applications/{app}/runs/{run}/cancel").json()["status"])  # I7
    return 1


def fail(what, r):
    print(what, "failed:", r.status_code, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
