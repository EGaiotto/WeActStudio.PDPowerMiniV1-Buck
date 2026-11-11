# PyInstaller — gera dist\PDPower Studio.exe (arquivo único, sem console)
#   .venv\Scripts\pyinstaller build_exe\pdpower_studio.spec --noconfirm
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = Path(SPECPATH).parent

hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("mcp", filter=lambda n: not n.startswith(("mcp.cli", "mcp.server.fastmcp")))
    + collect_submodules("mcp_types")
    + collect_submodules("webview")
    + ["serial.tools.list_ports_windows", "clr"]
)
datas = [(str(ROOT / "pdpower" / "web"), "pdpower/web")]
datas += collect_data_files("webview")
datas += collect_data_files("mcp")

a = Analysis(
    [str(ROOT / "build_exe" / "pdpower_studio.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PDPower Studio",
    icon=str(ROOT / "assets" / "pdpower.ico"),
    console=False,
    upx=False,
    version=str(ROOT / "build_exe" / "version_info.txt"),
)
