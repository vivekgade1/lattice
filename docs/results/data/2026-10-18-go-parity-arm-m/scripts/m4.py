import json,sys,subprocess
from pathlib import Path
import lattice
from lattice.gocheck import build_gocheck
mod=sys.argv[1]; out=Path(sys.argv[2])
b=build_gocheck()
def ext(req):
    p=subprocess.run([str(b),'extract'],input=json.dumps(req),capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    return p.stdout
out.mkdir(parents=True,exist_ok=True)
(out/'default.json').write_text(ext({"root":mod}))
try:
    (out/'policy.json').write_text(ext({"root":mod,"policy":True}))
except Exception as e: (out/'policy.err').write_text(str(e))
(out/'workflow.json').write_text(ext({"root":mod,"workflow":True}))
print(lattice.__file__, b)
