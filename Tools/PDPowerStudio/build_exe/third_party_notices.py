"""Gera THIRD_PARTY_NOTICES.md com as licenças das bibliotecas empacotadas no .exe.

Percorre as dependências de runtime (requirements.txt e as transitivas) instaladas no .venv
e copia o texto de licença distribuído por cada pacote.
    .venv\\Scripts\\python build_exe\\third_party_notices.py
"""
from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOTS = ["fastapi", "uvicorn", "pyserial", "mcp", "pywebview"]
# extras de uvicorn[standard] usados no Windows
EXTRA = ["websockets", "httptools", "watchfiles", "python-dotenv", "pyyaml", "colorama"]
# pacotes cujo projeto não publica arquivo de licença (só a declaração nos metadados)
NO_FILE = {
    "proxy_tools": "Declared as MIT License in the package metadata (classifier \"License :: OSI Approved :: MIT License\"); "
                   "the upstream project (github.com/jtushman/proxy_tools) does not ship a license file.",
}
SKIP_MARKER =re.compile(r"extra\s*==|sys_platform\s*==\s*['\"](darwin|linux)|platform_system\s*==\s*['\"](Darwin|Linux)")


def norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def deps(dist: metadata.Distribution) -> list[str]:
    out = []
    for r in dist.requires or []:
        if ";" in r and SKIP_MARKER.search(r.split(";", 1)[1]):
            continue
        name = re.split(r"[\s\[<>=!~;(]", r, 1)[0]
        out.append(name)
    return out


def license_text(dist: metadata.Distribution) -> str:
    files = dist.files or []
    cands = [f for f in files if re.search(r"(LICEN[CS]E|COPYING|NOTICE)", f.name, re.I)]
    texts = []
    for f in sorted(cands, key=lambda f: str(f)):
        try:
            texts.append(Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            pass
    return "\n\n".join(dict.fromkeys(texts))


def main():
    seen: dict[str, metadata.Distribution] = {}
    queue = ROOTS + EXTRA
    while queue:
        n = norm(queue.pop())
        if n in seen:
            continue
        try:
            d = metadata.distribution(n)
        except metadata.PackageNotFoundError:
            continue
        seen[n] = d
        queue += deps(d)

    parts = ["# Third-party notices\n",
             "PDPower Studio (MIT, see LICENSE) is distributed together with the components below. "
             "The Windows executable also bundles the Python runtime (PSF License) and the PyInstaller bootloader "
             "(GPL 2.0 with an exception that allows distributing applications built with it).\n",
             "Front-end: **uPlot 1.6.31** — MIT License, Copyright (c) 2022 Leon Sorokin "
             "(full text in `pdpower/web/vendor/uPlot.LICENSE`).\n",
             "| Package | Version | License |", "|---|---|---|"]
    rows, bodies = [], []
    for n in sorted(seen):
        d = seen[n]
        m = d.metadata
        lic = m.get("License-Expression") or ""
        if not lic:
            lic = next((c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")), "")
        if not lic:
            lic = (m.get("License") or "").splitlines()[0][:60] if m.get("License") else "see text"
        rows.append(f"| {m['Name']} | {d.version} | {lic} |")
        txt = license_text(d)
        extra = ROOT / "build_exe" / "licenses" / f"{m['Name']}.txt"
        if not txt and extra.exists():   # texto obtido do repositório oficial do pacote
            txt = extra.read_text(encoding="utf-8-sig").strip()
        if not txt and m["Name"] in NO_FILE:
            txt = NO_FILE[m["Name"]]
        bodies.append(f"## {m['Name']} {d.version}\n\nLicense: {lic}  \nHome: {m.get('Home-page') or next((u.split(',', 1)[1].strip() for u in m.get_all('Project-URL') or []), '')}\n\n"
                      + ("```\n" + txt + "\n```\n" if txt else "_(license file not shipped in the package metadata)_\n"))
    out = "\n".join(parts + rows) + "\n\n---\n\n" + "\n".join(bodies)
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text(out, encoding="utf-8")
    print(f"{len(seen)} pacotes")
    for r in rows:
        print(r)


if __name__ == "__main__":
    sys.exit(main())
