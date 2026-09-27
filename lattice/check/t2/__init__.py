"""T2 (`@procedure`): subset validation and the Lattice-owned type checker.

`split` separates a module's T2 statements (procedures, records, and -- in a program
that has any -- class definitions and standard-library imports) from its T0
statements; `run` checks the T2 part and returns a `T2Program` for T2b."""

from lattice.check.t2.model import CheckedProcedure, ProcSig, RecordDef, T2Program
from lattice.check.t2.module import run, split

__all__ = ["CheckedProcedure", "ProcSig", "RecordDef", "T2Program", "run", "split"]
