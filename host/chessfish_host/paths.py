"""Portable layout: config/, logs/, games/ live in the install dir, or %APPDATA%\\Chessfish if that isn't writable."""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# installed: <dir>\Chessfish.exe ; dev: repo root
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parents[2]
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # bundled data files


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    base = APP_DIR if FROZEN else APP_DIR / "data-host"
    if _writable(base / "config"):
        return base
    fallback = Path(os.environ.get("APPDATA", Path.home())) / "Chessfish"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


DATA = data_dir()
CONFIG_DIR, LOG_DIR, GAMES_DIR = DATA / "config", DATA / "logs", DATA / "games"
for _d in (CONFIG_DIR, LOG_DIR, GAMES_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def default_stockfish() -> Path:
    for p in (APP_DIR / "stockfish" / "stockfish.exe",
              APP_DIR / "build" / "stockfish" / "stockfish" / "stockfish-windows-x86-64-universal.exe"):
        if p.exists():
            return p
    return APP_DIR / "stockfish" / "stockfish.exe"
