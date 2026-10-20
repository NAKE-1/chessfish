import logging
import sys

from PySide6.QtWidgets import QApplication

from .config import Config, load_psk
from .gui.main_window import MainWindow
from .gui.wizard import SetupWizard
from .paths import LOG_DIR, RES_DIR


def main():
    logging.basicConfig(filename=LOG_DIR / "chessfish.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
