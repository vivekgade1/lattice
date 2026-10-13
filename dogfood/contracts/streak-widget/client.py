"""streak-widget: what a home-screen widget asks Streak for (today, one habit, a check-in)."""

import os
import sys
import uuid

import httpx


def main() -> int:
    c = httpx.Client(base_url=os.environ["STREAK_URL"], timeout=10)
    r = c.post(
        "/v1/auth/login",  # W1
        json={
            "email": os.environ["STREAK_EMAIL"],
            "password": os.environ["STREAK_PASSWORD"],
            "installation_id": os.environ["WIDGET_INSTALLATION"],
        },
    )
    if r.status_code != 200:
        return fail("login", r)
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    ttl = r.json()["expires_in"]
    me = c.get("/v1/me").json()  # W2
    home = c.get("/v1/home").json()  # W3
    print(f"user {me['id']} ({me['timezone']}) on {home['today']}; token valid {ttl} s")
    habits, params = [], {"limit": 50}
    while True:
        page = c.get("/v1/habits", params=params).json()  # W4
        habits += page["items"]
        if not page["has_more"]:
            break
        params = {"limit": 50, "cursor": page["next_cursor"]}
    want = os.environ.get("WIDGET_HABIT", "walk")
    habit = next((h for h in habits if h["name"] == want), None)
    if habit is None:
        print("no habit named", want, file=sys.stderr)
        return 1
    p = c.get(f"/v1/habits/{habit['id']}/progress").json()  # W5
    print(f"{want}: {p['done']} of {p['target']} ({p['status']})")
    if os.environ.get("WIDGET_CHECK_IN") == "1":
        r = c.post(
            f"/v1/habits/{habit['id']}/completions",  # W6
            json={"id": str(uuid.uuid4()), "date": home["today"]},
        )
        if r.status_code not in (200, 201):
            return fail("check-in", r)
        print("checked in", r.json()["id"], "for", r.json()["date"])
    if home["nudges"] and os.environ.get("WIDGET_DISMISS") == "1":
        nudge = home["nudges"][0]["id"]
        print("dismissed", c.post(f"/v1/nudges/{nudge}/dismiss").json()["id"])  # W7
    return 0


def fail(what, r):
    print(what, "failed:", r.status_code, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
