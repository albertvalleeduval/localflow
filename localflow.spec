# -*- mode: python ; coding: utf-8 -*-
"""Build PyInstaller : deux exécutables dans un dossier commun.

    pyinstaller localflow.spec --noconfirm

- `localflow.exe`     : le démon (app.py), sans fenêtre de console ;
- `localflow-ui.exe`  : la fenêtre (ui.py), lancée par l'icône de zone de
  notification ou à la main.

Un seul dossier `dist/localflow/` : les deux exes côte à côte, les
dépendances partagées dans `_internal/`. Les fichiers de l'utilisateur
(config.json, history.jsonl, localflow.log) sont créés à côté des exes au
premier lancement (voir runtime.py). Le modèle est téléchargé au premier
lancement dans le cache HuggingFace de l'utilisateur, jamais dans ce dossier.
"""

from PyInstaller.utils.hooks import collect_all

# onnx_asr choisit modèle et prétraitement dynamiquement : on embarque tout
# le paquet (code, données, dépendances déclarées) plutôt que de courir
# après chaque import tardif.
onnx_asr = collect_all("onnx_asr")

datas = [
    ("web", "web"),
    ("config.example.json", "."),
] + onnx_asr[0]

binaries = onnx_asr[1]
hiddenimports = onnx_asr[2] + [
    "win32com.client",   # importé dans une fonction (ui.py, install_startup)
]

common = dict(
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

a_daemon = Analysis(["app.py"], **common)
a_window = Analysis(["ui.py"], **common)

pyz_daemon = PYZ(a_daemon.pure)
pyz_window = PYZ(a_window.pure)

exe_daemon = EXE(
    pyz_daemon,
    a_daemon.scripts,
    [],
    exclude_binaries=True,
    name="localflow",
    icon="web/icon.ico",
    console=False,          # équivalent pythonw : le journal suffit
    upx=False,
)

exe_window = EXE(
    pyz_window,
    a_window.scripts,
    [],
    exclude_binaries=True,
    name="localflow-ui",
    icon="web/icon.ico",
    console=False,
    upx=False,
)

coll = COLLECT(
    exe_daemon,
    a_daemon.binaries,
    a_daemon.datas,
    exe_window,
    a_window.binaries,
    a_window.datas,
    name="localflow",
    upx=False,
)
