"""``python -m lattice.studio PROJECT_DIR [--port N] [--model M]``: the real Studio server
(lattice.studio.server). ``python -m lattice.studio --mock [FIXTURE_DIR] [--port N]``:
the fixture-backed mock server (lattice.studio.mock_server)."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--mock" in args:
        from lattice.studio import mock_server

        i = args.index("--mock")
        rest = args[:i] + args[i + 1 :]
        if i + 1 < len(args) and not args[i + 1].startswith("-"):
            rest = args[:i] + ["--fixtures", args[i + 1]] + args[i + 2 :]
        mock_server.main(rest)
        return 0
    from lattice.studio import server

    return server.main(args)


if __name__ == "__main__":
    sys.exit(main())
