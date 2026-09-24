# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: gera o sidecar `edgefut-engine` (onefile) usado pelo Tauri.

Uso: cd engine && .venv/Scripts/python -m PyInstaller edgefut-engine.spec
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

hidden = []
datas = []
binaries = []
for pkg in ("duckdb", "pyarrow", "scipy", "pandas", "numpy", "sqlalchemy", "apscheduler", "uvicorn", "httpx", "anyio", "pydantic", "pydantic_settings"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
hidden += collect_submodules("edgefut")
hidden += ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"]

a = Analysis(
    ["edgefut_launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "pytest", "playwright", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="edgefut-engine",
    debug=False,
    strip=False,
    upx=False,
    console=True,  # o Tauri esconde a janela; console=True facilita logs em dev
    disable_windowed_traceback=False,
)
