"""Minimal `.env` loader so the benchmark can pick up ANTHROPIC_API_KEY without a dependency."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path = REPO_ROOT / ".env") -> dict[str, str]:
    """Set KEY=VALUE pairs from `path` into os.environ; returns the keys it set.

    Variables already in the environment win, empty values are skipped, and a missing
    file is a no-op. Supports `# comments`, `export KEY=...`, and quoted values.
    """
    if not path.is_file():
        return {}
    loaded: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
