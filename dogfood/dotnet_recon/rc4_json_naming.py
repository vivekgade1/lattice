"""RC4: the effective System.Text.Json naming policy of a project (docs/results/2026-10-18-dotnet-prereg.md,
5.1 and N5; expected for D1: ``SnakeCaseLower`` or per-property ``[JsonPropertyName]``).

    python dogfood/dotnet_recon/rc4_json_naming.py <project-dir> [--out FILE]

Reads ``*.cs`` only (comments stripped): every ``PropertyNamingPolicy = <expr>`` assignment (from
``ConfigureHttpJsonOptions``, ``AddJsonOptions``, ``JsonSerializerOptions`` initialisers), custom
``JsonNamingPolicy`` subclasses, ``JsonSerializerDefaults.Web`` and the ``[JsonPropertyName("x")]`` overrides.
The verdict follows N5: all assignments equal -> that policy; none -> ``CamelCase`` (the web default, only for the
ASP.NET hosts); ``null`` -> as declared; a custom subclass -> ``unread``; differing assignments -> ``mixed``.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

KNOWN = {"CamelCase", "SnakeCaseLower", "SnakeCaseUpper", "KebabCaseLower", "KebabCaseUpper"}
ASSIGN = re.compile(r"\bPropertyNamingPolicy\s*=\s*([^;,})]+)")
SUBCLASS = re.compile(r"\bclass\s+(\w+)\s*:\s*(?:[\w.]*\.)?JsonNamingPolicy\b")
PROPNAME = re.compile(r'\[\s*(?:[\w.]*\.)?JsonPropertyName\(\s*"([^"]*)"\s*\)\s*\]')
DEFAULTS = re.compile(r"\bJsonSerializerDefaults\.(Web|General)\b")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("root")
    ap.add_argument("--out")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    assigns, custom, overrides, defaults = [], [], [], []
    for p in C.source_files(root, ".cs"):
        rel = p.relative_to(root).as_posix()
        text = strip_comments(p.read_text(encoding="utf-8-sig"))
        for rx, sink in ((SUBCLASS, custom), (DEFAULTS, defaults)):
            for m in rx.finditer(text):
                sink.append(
                    {"file": rel, "line": text.count("\n", 0, m.start()) + 1, "what": m.group(0)}
                )
        for m in ASSIGN.finditer(text):
            expr = m.group(1).strip()
            name = (
                expr.rsplit(".", 1)[-1]
                if re.fullmatch(r"(?:[\w.]*\.)?JsonNamingPolicy\.\w+", expr)
                else expr
            )
            assigns.append(
                {
                    "file": rel,
                    "line": text.count("\n", 0, m.start()) + 1,
                    "expr": expr,
                    "policy": name,
                }
            )
        for m in PROPNAME.finditer(text):
            overrides.append(
                {"file": rel, "line": text.count("\n", 0, m.start()) + 1, "name": m.group(1)}
            )
    policies = sorted({a["policy"] for a in assigns})
    if custom:
        verdict = "unread (custom JsonNamingPolicy subclass: every wire name unread)"
    elif not assigns:
        verdict = "CamelCase (web default)"
    elif len(policies) == 1 and policies[0] == "null":
        verdict = "null (as declared)"
    elif len(policies) == 1 and policies[0] in KNOWN:
        verdict = policies[0]
    elif len(policies) == 1:
        verdict = f"unread ({policies[0]})"
    else:
        verdict = "mixed: " + ", ".join(policies)
    C.emit(
        {
            "rc": "RC4",
            "root": root.name,
            "assignments": assigns,
            "policies": policies,
            "custom_policy_classes": custom,
            "serializer_defaults": defaults,
            "json_property_name_overrides": overrides,
            "overrides_count": len(overrides),
            "verdict": verdict,
        },
        args.out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
