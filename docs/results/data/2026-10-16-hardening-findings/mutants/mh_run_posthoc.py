"""POST HOC: MH-1..MH-14 with MH-13's stale old text corrected in memory (the file is not owned)."""
import sys
sys.path.insert(0, "/Users/vivekgade/Documents/lattice-hd")
from tests.studio import test_mcp_intake_mutants as m

SE = m.SE
old = "        (*_READ_TOOLS, *_MODEL_TOOLS, *_MULTI_TOOLS, *_CHANGE_TOOLS),\n"
new = "        (*_READ_TOOLS, *_MODEL_TOOLS, *_MULTI_TOOLS, *_IMPL_TOOLS, *_CHANGE_TOOLS),\n"
mut = "        (*_READ_TOOLS, *_MODEL_TOOLS, *_IMPL_TOOLS, *_CHANGE_TOOLS),  # MUTANT MH-13 (post hoc text)\n"
what, edits = m.MUTANTS["MH-13"]
assert edits[0][1] == old
m.MUTANTS["MH-13"] = (what, [(SE, new, mut)])
sys.exit(m.main(sys.argv[1:]))
