"""A toy background service for tests/spec/test_services.py: ``python services_worker.py
DB_PATH``. Its pid (and a child's, in mode ``spawn``) go to ``$WORKER_PIDFILE``, one per
line. ``$WORKER_MODE``:

* ``work`` (default): loops, moving every queued run of ``runs`` to running, then to
  succeeded; exits on SIGTERM;
* ``noop``: loops doing nothing; exits on SIGTERM;
* ``stubborn``: does nothing and ignores SIGTERM (only SIGKILL ends it);
* ``spawn``: as ``work``, after starting a child process that sleeps (and ignores
  SIGTERM) -- the process group must be stopped as a whole;
* ``violate``: queues two runs of config 2 (breaking "at most one live run");
* ``crash``: exits with code 3 at once, saying why on stderr.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time


def main() -> int:
    db = sys.argv[1]
    mode = os.environ.get("WORKER_MODE", "work")
    pids = [os.getpid()]
    if mode == "crash":
        print("worker: cannot open the queue", file=sys.stderr)
        return 3
    if mode == "spawn":
        child = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c",
             "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
             "time.sleep(60)"]  # fmt: skip
        )
        pids.append(child.pid)
    pidfile = os.environ.get("WORKER_PIDFILE")
    if pidfile:
        with open(pidfile, "w") as f:
            f.write("\n".join(str(p) for p in pids) + "\n")
    if mode == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if mode == "violate":
        conn = sqlite3.connect(db, timeout=10)
        conn.execute("INSERT INTO runs (config_id, status) VALUES (2, 'queued')")
        conn.execute("INSERT INTO runs (config_id, status) VALUES (2, 'queued')")
        conn.commit()
        conn.close()
    while True:
        if mode in ("work", "spawn"):
            conn = sqlite3.connect(db, timeout=10)
            try:
                conn.execute("UPDATE runs SET status = 'running' WHERE status = 'queued'")
                conn.commit()
                conn.execute("UPDATE runs SET status = 'succeeded' WHERE status = 'running'")
                conn.commit()
            finally:
                conn.close()
        time.sleep(0.05)


if __name__ == "__main__":
    sys.exit(main())
