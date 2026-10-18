import json,sys
from pathlib import Path
from lattice.gocheck import GoPolicy, run_gocheck
from spec import SPEC
pol=GoPolicy.from_spec(SPEC)
for f in run_gocheck(Path(sys.argv[1]),pol,strict=True): print(f.code,f.key,f.file,f.line)
print('done')
