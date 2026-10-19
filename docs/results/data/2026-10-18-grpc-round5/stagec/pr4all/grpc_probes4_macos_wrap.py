"""Round 4's runner, unmodified, plus the one platform shim round 5's pf5_leak also applies.

The stray-process reader (round 3's reads /proc, which macOS lacks) is replaced by round 5's
ps-based stray5. The file name keeps "grpc_probes" so that stray5 skips this process.
"""

import runpy
import sys

sys.path.insert(0, "/Users/vivekgade/Documents/lattice-grb/dogfood")
import grpc_probes3 as p3
import grpc_probes5 as p5

p3._stray3 = p5.stray5
sys.argv[0] = "/Users/vivekgade/Documents/lattice-grb/dogfood/grpc_probes4.py"
runpy.run_path(sys.argv[0], run_name="__main__")
