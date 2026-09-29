# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：把 `server/`（连带 `core/` 引擎 + 根目录 `aes_stdlib.py`）打成
`dist/cx-sidecar/`（onedir），release 包里放到 `sidecar/` 目录下，由 shell.exe 在打包态按
`<exe_dir>/sidecar/cx-sidecar.exe` 拉起（见 shell/src-tauri/src/sidecar.rs resolve_launch 分支 2）。

构建（在仓库根跑）：
    python -m PyInstaller --noconfirm --clean sidecar.spec
产物：dist/cx-sidecar/{cx-sidecar.exe, _internal/...}

为什么要手写 hiddenimports：uvicorn 的事件循环 / 协议实现 / lifespan 都是**按名字动态 import**
（`uvicorn.loops.auto` 里按字符串挑 asyncio/uvloop），静态分析看不到，漏了就是启动即崩。
"""
import os

ROOT = os.path.abspath(os.getcwd())

HIDDEN = [
    # uvicorn：动态挑选实现，必须显式列
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    # 上面这些依赖的底层实现
    "h11", "anyio", "anyio._backends._asyncio",
    "email.mime.multipart", "email.mime.text",
]

a = Analysis(
    ["server/__main__.py"],
    pathex=[ROOT],                       # 让 `server` / `core` / `aes_stdlib` 都能解析
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    # 只排与本 sidecar 无关的重家伙：旧 Flet GUI、测试框架、GUI 工具包。
    # 不排 PIL/numpy —— 识图链可能间接用到，宁大勿崩。
    excludes=["flet", "pytest", "tkinter", "matplotlib", "IPython", "notebook"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cx-sidecar",
    debug=False,
    strip=False,
    upx=False,              # upx 会破坏部分 pyd 签名且杀软误报，关掉
    console=True,           # 必须留控制台：stdout 要吐 CX_READY 握手行
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="cx-sidecar",
)
