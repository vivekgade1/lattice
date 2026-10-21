"""RC2: the implicit usings of ``Microsoft.NET.Sdk`` and ``Microsoft.NET.Sdk.Web`` for the installed SDK,
read from the SDK's own props files (docs/results/2026-10-18-dotnet-prereg.md, 5.1 and N1).

    python dogfood/dotnet_recon/rc2_implicit_usings.py [--out FILE] [--csproj PROJECT.csproj]

With ``--csproj`` the list is the project's: the base list, the Web list when the project's SDK is
``Microsoft.NET.Sdk.Web``, plus its own ``<Using Include=...>`` items (``ImplicitUsings`` must be enable/true).
The answer records the sha256 of both props files, so a different SDK is visible as a different hash.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

BASE_PROPS = "Sdks/Microsoft.NET.Sdk/targets/Microsoft.NET.Sdk.CSharp.props"
WEB_PROPS = "Sdks/Microsoft.NET.Sdk.Web/Targets/Sdk.Server.props"


def sdk_dir() -> Path:
    version = C.sdk_version()
    path = C.dotnet_root() / "sdk" / version
    if not path.is_dir():
        sys.exit(f"RC2: SDK directory not found: {path}")
    return path


def read_usings(props: Path) -> list[dict[str, str | None]]:
    """The ``<Using Include>`` items of every ItemGroup conditioned on ImplicitUsings, in file order."""
    root = ET.fromstring(props.read_text(encoding="utf-8-sig"))
    ns = re.match(r"\{.*\}", root.tag)
    pre = ns.group(0) if ns else ""
    items: list[dict[str, str | None]] = []
    for group in root.iter(pre + "ItemGroup"):
        if "ImplicitUsings" not in (group.get("Condition") or ""):
            continue
        for use in group.findall(pre + "Using"):
            items.append({"include": use.get("Include") or "", "condition": use.get("Condition")})
    return items


def project_usings(csproj: Path) -> tuple[bool, bool, list[str]]:
    text = csproj.read_text(encoding="utf-8-sig")
    web = bool(re.search(r'<Project\s+Sdk="Microsoft\.NET\.Sdk\.Web"', text))
    enabled = bool(re.search(r"<ImplicitUsings>\s*(enable|true)\s*</ImplicitUsings>", text))
    own = re.findall(r'<Using\s+Include="([^"]+)"', text)
    return web, enabled, own


def implicit_usings(csproj: Path | None, web: bool = True) -> list[str]:
    """The global usings of a project (``csproj``) or of a Sdk.Web project (``web``)."""
    sdk = sdk_dir()
    own: list[str] = []
    if csproj is not None:
        web, enabled, own = project_usings(csproj)
        if not enabled:
            return own
    names = [u["include"] or "" for u in read_usings(sdk / BASE_PROPS)]
    if web:
        names += [u["include"] or "" for u in read_usings(sdk / WEB_PROPS)]
    return names + [o for o in own if o not in names]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out")
    ap.add_argument("--csproj")
    args = ap.parse_args()
    sdk = sdk_dir()
    base = read_usings(sdk / BASE_PROPS)
    web = read_usings(sdk / WEB_PROPS)
    result = {
        "rc": "RC2",
        "sdk": C.sdk_version(),
        "sources": {
            BASE_PROPS: C.sha256_file(sdk / BASE_PROPS),
            WEB_PROPS: C.sha256_file(sdk / WEB_PROPS),
        },
        "microsoft_net_sdk": base,
        "microsoft_net_sdk_web": web,
        "web_project_list": [u["include"] for u in base + web],
    }
    if args.csproj:
        result["project"] = args.csproj
        result["project_list"] = implicit_usings(Path(args.csproj))
    C.emit(result, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
