# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Comp Agent desktop app.

ONEDIR build (deliberately NOT onefile: onefile unpacks to %TEMP% on every
launch, which is slow over VPN and noisier for antivirus; onedir launches
fast even from a network share).

Build, from the repo root, inside a venv that has the project plus the
packaging extra installed (``pip install -e ".[packaging]"``):

    python -m PyInstaller packaging/comp_agent.spec --noconfirm

Output lands in ``dist/CompAgent/`` (redirect with ``--distpath``/
``--workpath`` to keep the repo clean). See packaging/DEPLOY.md for the
deploy layout on the office share.
"""

import glob
import os

# SPECPATH is injected by PyInstaller: the directory containing this spec.
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
SRC_DIR = os.path.join(REPO_ROOT, "src")
ENTRY_SCRIPT = os.path.join(SRC_DIR, "comp_agent", "app.py")

# Bundle the deck assets (the Pelli logo). deck.py resolves them through its
# freeze-safe _resource_path helper: sys._MEIPASS/comp_agent/assets/... when
# frozen, package-relative when running from source.
datas = [
    (path, os.path.join("comp_agent", "assets"))
    for path in glob.glob(os.path.join(SRC_DIR, "comp_agent", "assets", "*.jpg"))
]

# Include the package dist-info so importlib.metadata can report the real
# version for --version inside the frozen app. Best effort: fall back to the
# baked-in version constant in app.py when metadata is unavailable.
try:
    from PyInstaller.utils.hooks import copy_metadata

    datas += copy_metadata("comp-agent")
except Exception:
    pass

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[SRC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # ui.py imports tkinter lazily inside the folder-picker request
        # handler; list it explicitly so the tkinter hook always runs and
        # bundles the Tcl/Tk runtime.
        "tkinter",
        "tkinter.filedialog",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CompAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # v1 ships with a console window: it doubles as the status/log surface
    # ("Comp Agent is running at ... / Press Ctrl+C to stop").
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CompAgent",
)
