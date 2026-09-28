"""Language-neutral IR for checked T2 procedures.

`lattice.t2ir.nodes` holds the frozen dataclass node set; `lattice.t2ir.dump` renders a
stable text form of it for golden tests. Task 4 lowers checked T2 procedures into this
IR; the Python and Go emitters read it. The node names and fields here are a contract:
no field may hold a Python `ast` object, and every expression node's last field is
`type: T2Type`.
"""

from __future__ import annotations
