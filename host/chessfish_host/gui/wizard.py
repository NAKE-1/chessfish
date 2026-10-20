"""First-run wizard: Stockfish check, hardware defaults, pairing key, server test."""
import asyncio
import re
import threading
import time

import psutil
from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import (QApplication, QFormLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWizard,
                               QWizardPage)
from chessfish_proto.crypto import psk_from_text
from chessfish_proto.link_socket import HandshakeError, dial

from ..config import Config
from .dialogs import PairingWidget

INSTALL_URL = "https://raw.githubusercontent.com/NAKE-1/chessfish/main/deploy/install.sh"


def default_hash_mb() -> int:
    quarter = psutil.virtual_memory().total // 4 // 2 ** 20
    return 2 ** max(4, min(4096, quarter).bit_length() - 1)  # power of two, <= 4096


def default_threads() -> int:
    return max(1, (psutil.cpu_count(logical=True) or 2) - 1)


class StockfishPage(QWizardPage):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setTitle("Stockfish")
        self.setSubTitle("The bundled universal build picks the best code path for this CPU by itself. Run a quick bench to check it works.")
        lay = QFormLayout(self)
        self.path = QLineEdit(cfg.stockfish_path)
        self.btn = QPushButton("Run bench")
        self.result = QLabel("not run yet")
        self.btn.clicked.connect(self.bench)
        lay.addRow("Path", self.path)
        lay.addRow(self.btn, self.result)
        self.ok = False
        self.proc = None

    def bench(self):
        self.ok = False
        self.completeChanged.emit()
        self.result.setText("running…")
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.finished.connect(self._done)
        self.proc.errorOccurred.connect(lambda _: self.result.setText(f"could not run: {self.proc.errorString()}"))
        self.proc.start(self.path.text().strip(), ["bench", "16", "1", "10"])

    def _done(self, code, _status):
        out = bytes(self.proc.readAll()).decode(errors="replace")
        nps = re.search(r"Nodes/second\s*:\s*(\d+)", out)
        name = re.search(r"^(Stockfish [^\n]*)", out, re.M)
        if code == 0 and nps:
            self.ok = True
            self.result.setText(f"OK: {name.group(1) if name else 'Stockfish'} · {int(nps.group(1)):,} nodes/s (1 thread)")
        else:
            self.result.setText(f"bench failed (exit {code})")
        self.completeChanged.emit()

    def isComplete(self):
        return self.ok

    def validatePage(self):
        self.cfg.stockfish_path = self.path.text().strip()
        return True


class HardwarePage(QWizardPage):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setTitle("Hardware")
        mem = psutil.virtual_memory().total / 2 ** 30
        self.setSubTitle(f"{psutil.cpu_count(logical=True)} logical cores, {mem:.1f} GB RAM detected.")
        lay = QFormLayout(self)
        self.threads = QSpinBox(minimum=1, maximum=1024, value=default_threads())
        self.hash = QSpinBox(minimum=16, maximum=65536, value=default_hash_mb(), suffix=" MB")
        lay.addRow("Analysis threads", self.threads)
        lay.addRow("Hash", self.hash)

    def validatePage(self):
        self.cfg.threads, self.cfg.hash_mb = self.threads.value(), self.hash.value()
        return True


class KeyPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Pairing key")
        self.setSubTitle("The server only talks to a PC that knows this key. Generate one here and put it in the server's .env, "
                         "or paste the key the server already uses.")
        lay = QVBoxLayout(self)
        self.pair = PairingWidget()
        self.pair.edit.textChanged.connect(self.completeChanged)
        lay.addWidget(self.pair)
        lay.addWidget(QLabel("It is stored in Windows Credential Manager, never in a plain file."))
        lay.addWidget(QLabel("<b>On the Docker VM</b>, run this once to install (or update) the server with this key:"))
        self.cmd = QLineEdit(readOnly=True)
        copy = QPushButton("Copy command")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.cmd.text()))
        lay.addWidget(self.cmd)
        lay.addWidget(copy)
        self.pair.edit.textChanged.connect(self._cmd)
        self._cmd()

    def _cmd(self):
        key = self.pair.edit.text().strip() if self.pair.valid() else "<pairing-key>"
        self.cmd.setText(f"curl -fsSL {INSTALL_URL} | sh -s -- {key}")

    def isComplete(self):
        return self.pair.valid()

    def validatePage(self):
        self.pair.save()
        return True


class _Tester(QObject):
    done = Signal(str)


class ServerPage(QWizardPage):
    def __init__(self, cfg: Config, key_page: KeyPage):
        super().__init__()
        self.cfg, self.key_page = cfg, key_page
        self.setTitle("Server")
        self.setSubTitle("Address of the VM running chessfish-server (IP or name), and the link port.")
        lay = QFormLayout(self)
        self.host = QLineEdit(cfg.server_host)
        self.port = QSpinBox(minimum=1, maximum=65535, value=cfg.link_port)
        self.btn = QPushButton("Test connection")
        self.result = QLabel("You can also skip this and test later.")
        self.result.setWordWrap(True)
        self.btn.clicked.connect(self.test)
        lay.addRow("Address", self.host)
        lay.addRow("Link port", self.port)
        lay.addRow(self.btn, self.result)
        self.tester = _Tester()
        self.tester.done.connect(self.result.setText)

    def test(self):
        url = f"ws://{self.host.text().strip()}:{self.port.value()}/link"
        psk = psk_from_text(self.key_page.pair.edit.text())
        self.result.setText("testing…")
        threading.Thread(target=lambda: self.tester.done.emit(asyncio.run(test_connection(url, psk))), daemon=True).start()

    def validatePage(self):
        self.cfg.server_host, self.cfg.link_port = self.host.text().strip(), self.port.value()
        return True


async def test_connection(url: str, psk: bytes) -> str:
    from chessfish_proto import messages as lm
    try:
        sock = await dial(url, psk)
    except HandshakeError:
        return "✗ Handshake rejected: the server has a different pairing key."
    except Exception as e:
        return f"✗ Unreachable: {e or type(e).__name__}"
    try:
        t = time.monotonic()
        await sock.send(lm.Ping(seq=1, t_send=t))
        while True:
            m = await asyncio.wait_for(sock.recv(), 5)
            if isinstance(m, lm.Pong):
                return f"✓ Connected, handshake OK, RTT {(time.monotonic() - t) * 1000:.0f} ms"
    except Exception as e:
        return f"✗ Connected but no reply: {e or type(e).__name__}"
    finally:
        await sock.close()


class SetupWizard(QWizard):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Chessfish setup")
        self.setMinimumSize(640, 420)
        key = KeyPage()
        for page in (StockfishPage(cfg), HardwarePage(cfg), key, ServerPage(cfg, key)):
            self.addPage(page)

    def accept(self):
        self.cfg.wizard_done = True
        self.cfg.save()
        super().accept()
