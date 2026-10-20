# PyInstaller spec: onedir, windowed. Build with installer/build.ps1 (or: pyinstaller installer/chessfish.spec).
from pathlib import Path

ROOT = Path(SPECPATH).parent
PKG = ROOT / "host" / "chessfish_host"

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    pathex=[str(ROOT / "host"), str(ROOT / "shared")],
    datas=[
        (str(PKG / "data"), "data"),
        (str(PKG / "gui" / "theme.qss"), "gui"),
        (str(PKG / "dashboard" / "static"), "dashboard/static"),
    ],
    hiddenimports=["keyring.backends.Windows"],
    excludes=[
        "tkinter", "matplotlib", "IPython", "pytest", "playwright",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
        "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtNfc", "PySide6.QtRemoteObjects",
        "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtWebSockets", "PySide6.QtHttpServer",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Chessfish", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Chessfish", upx=False)
