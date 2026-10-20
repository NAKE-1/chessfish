import logging
import os
import sys

from PySide6.QtWidgets import QApplication

from .config import Config, load_psk
from .gui.main_window import MainWindow
from .gui.wizard import SetupWizard
from .paths import LOG_DIR, RES_DIR


def selftest() -> int:
    """`Chessfish.exe --selftest`: checks a frozen build has everything it imports lazily."""
    import chess
    import keyring
    import uvicorn  # noqa: F401  (dashboard)
    import websockets  # noqa: F401  (link)

    from .dashboard.server import Dashboard
    from .stats.openings import lookup
    Dashboard()
    assert lookup(chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"))
    assert (RES_DIR / "gui" / "theme.qss").exists()
    logging.info("selftest ok, keyring backend %s", keyring.get_keyring())
    return 0


def main():
    # windowed exe: no console, so stdout/stderr are None and libraries calling .isatty()/.write() crash
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w")
    logging.basicConfig(filename=LOG_DIR / "chessfish.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if "--selftest" in sys.argv:
        return selftest()
    app = QApplication(sys.argv)
    app.setApplicationName("Chessfish")
    app.setStyleSheet((RES_DIR / "gui" / "theme.qss").read_text("utf-8"))
    cfg = Config.load()
    if not cfg.wizard_done or not load_psk():
        if SetupWizard(cfg).exec() != SetupWizard.Accepted:
            return 0
    win = MainWindow(cfg)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
