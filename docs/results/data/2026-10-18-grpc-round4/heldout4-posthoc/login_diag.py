import importlib.util, os, sys, tempfile, json
from pathlib import Path
sys.dont_write_bytecode = True
scr = Path(tempfile.mkdtemp(prefix="diag-"))
os.environ["LATTICE_HELDOUT_BUILD"] = str(scr/"hb"); os.environ["APP_BIN_DIR"] = str(scr/"bin")
app_dir = Path(sys.argv[1]); mode = sys.argv[2]
sys.path.insert(0, str(app_dir))
sp = importlib.util.spec_from_file_location("hh", app_dir/"harness.py"); h = importlib.util.module_from_spec(sp); sys.modules["hh"]=h; sp.loader.exec_module(h)
p = scr/"c.db"
ret = h.build_db(p)
print("build_db returned:", repr(ret), "| db file exists:", p.exists())
import sqlite3
print("members in built db:", sqlite3.connect(p).execute("select count(*) from members").fetchone()[0])
db = ret if mode == "as_is" else p
app = h.make_app(db)
print("make_app db_path:", repr(app.db_path))
rt = h.RUNTIME
try:
    with rt.open(app) as tr:
        for handle in h.USERS:
            try:
                rt.auth.token(tr, handle, None); print("login", handle, "ok")
            except Exception as e: print("login", handle, "failed:", type(e).__name__, str(e)[:120])
finally:
    rt.close(app)
