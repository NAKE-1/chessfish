from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)
from chessfish_proto.crypto import gen_psk, psk_from_text, psk_to_text

from ..config import Config, load_psk, save_psk

PRESETS = [("1+0", 60, 0), ("3+2", 180, 2), ("5+3", 300, 3), ("10+5", 600, 5), ("15+10", 900, 10),
           ("30+0", 1800, 0), ("Untimed", 0, 0), ("Custom", None, None)]


def _spin(lo, hi, val, suffix=""):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


class GameSetupDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("New game")
        form = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItem("Engine plays the friend (Stockfish)", "engine")
        self.mode.addItem("I play the friend (Stockfish analyses)", "human")
        self.mode.setCurrentIndex(0 if cfg.mode == "engine" else 1)
        self.color = QComboBox()
        for c in ("random", "white", "black"):
            self.color.addItem(c.capitalize(), c)
        self.color.setCurrentIndex(["random", "white", "black"].index(cfg.host_color))
        self.preset = QComboBox()
        for name, ini, inc in PRESETS:
            self.preset.addItem(name, (ini, inc))
        self.initial = _spin(0, 180, cfg.tc_initial_s // 60, " min")
        self.inc = _spin(0, 60, cfg.tc_increment_s, " s")
        custom = QWidget()
        h = QHBoxLayout(custom)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.initial)
        h.addWidget(QLabel("+"))
        h.addWidget(self.inc)
        self.preset.currentIndexChanged.connect(self._preset)
        idx = next((i for i, (_, ini, inc) in enumerate(PRESETS)
                    if (cfg.untimed and ini == 0) or (not cfg.untimed and ini == cfg.tc_initial_s and inc == cfg.tc_increment_s)),
                   len(PRESETS) - 1)
        self.preset.setCurrentIndex(idx)
        self._preset(idx)

        self.use_skill = QCheckBox("Use Skill Level instead of Elo")
        self.use_skill.setChecked(cfg.use_skill)
        self.elo = QSlider(Qt.Horizontal)
        self.elo.setRange(1320, 3190)
        self.elo.setValue(cfg.elo)
        self.elo_label = QLabel(str(cfg.elo))
        self.elo.valueChanged.connect(lambda v: self.elo_label.setText(str(v)))
        elo_row = QWidget()
        h = QHBoxLayout(elo_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.elo)
        h.addWidget(self.elo_label)
        self.skill = _spin(0, 20, cfg.skill)
        self.humanize = QCheckBox("Humanized think time")
        self.humanize.setChecked(cfg.humanize)
        self.min_ms = _spin(100, 60000, cfg.min_ms, " ms")
        self.max_ms = _spin(500, 300000, cfg.max_ms, " ms")

        form.addRow("Mode", self.mode)
        form.addRow("Host plays", self.color)
        form.addRow("Time control", self.preset)
        form.addRow("Custom", custom)
        form.addRow(self.use_skill)
        form.addRow("Engine Elo", elo_row)
        form.addRow("Skill level", self.skill)
        form.addRow(self.humanize)
        form.addRow("Min delay", self.min_ms)
        form.addRow("Max delay", self.max_ms)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Start")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.mode.currentIndexChanged.connect(self._mode)
        self.use_skill.toggled.connect(self._mode)
        self._mode()

    def _preset(self, i):
        ini, inc = self.preset.itemData(i)
        custom = ini is None
        self.initial.setEnabled(custom)
        self.inc.setEnabled(custom)
        if not custom and ini:
            self.initial.setValue(ini // 60)
            self.inc.setValue(inc)

    def _mode(self):
        engine = self.mode.currentData() == "engine"
        for w in (self.use_skill, self.humanize, self.min_ms, self.max_ms):
            w.setEnabled(engine)
        self.elo.setEnabled(engine and not self.use_skill.isChecked())
        self.skill.setEnabled(engine and self.use_skill.isChecked())

    def apply(self):
        c = self.cfg
        c.mode, c.host_color = self.mode.currentData(), self.color.currentData()
        ini, _ = self.preset.currentData()
        c.untimed = ini == 0
        c.tc_initial_s, c.tc_increment_s = self.initial.value() * 60, self.inc.value()
        c.use_skill, c.elo, c.skill = self.use_skill.isChecked(), self.elo.value(), self.skill.value()
        c.humanize, c.min_ms, c.max_ms = self.humanize.isChecked(), self.min_ms.value(), max(self.min_ms.value(), self.max_ms.value())
        c.save()


class PairingWidget(QWidget):
    """Generate or paste the pairing key; shows it with a copy button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(load_psk() or "")
        self.edit.setPlaceholderText("paste a key, or generate a new one")
        row = QHBoxLayout()
        gen, copy = QPushButton("Generate new key"), QPushButton("Copy")
        gen.clicked.connect(lambda: self.edit.setText(psk_to_text(gen_psk())))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.edit.text().strip()))
        row.addWidget(gen)
        row.addWidget(copy)
        lay.addWidget(self.edit)
        lay.addLayout(row)

    def valid(self) -> bool:
        try:
            psk_from_text(self.edit.text())
            return True
        except ValueError:
            return False

    def save(self):
        save_psk(self.edit.text().strip())


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        self.host = QLineEdit(cfg.server_host)
        self.port = _spin(1, 65535, cfg.link_port)
        self.pair = PairingWidget()
        self.sf = QLineEdit(cfg.stockfish_path)
        browse = QPushButton("…")
        browse.clicked.connect(lambda: (p := QFileDialog.getOpenFileName(self, "Stockfish", "", "Programs (*.exe)")[0]) and self.sf.setText(p))
        sf_row = QWidget()
        h = QHBoxLayout(sf_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.sf)
        h.addWidget(browse)
        self.threads = _spin(1, 1024, cfg.threads)
        self.hash = _spin(16, 65536, cfg.hash_mb, " MB")
        self.multipv = _spin(1, 10, cfg.multipv)
        self.friend_elo = _spin(400, 3200, cfg.friend_elo_estimate)
        self.dashboard = QCheckBox("Local web dashboard (http://127.0.0.1:6769)")
        self.dashboard.setChecked(cfg.dashboard)
        self.lan = QCheckBox("Also listen on the LAN (off = this PC only)")
        self.lan.setChecked(cfg.dashboard_lan)
        self.games = QLineEdit(cfg.games_dir)
        self.grace = _spin(10, 3600, cfg.grace_s, " s")
        self.countdown = _spin(0, 60, cfg.countdown_s, " s")
        self.heartbeat = _spin(500, 10000, cfg.heartbeat_ms, " ms")
        form.addRow("Server address", self.host)
        form.addRow("Link port", self.port)
        form.addRow("Pairing key", self.pair)
        form.addRow("Stockfish", sf_row)
        form.addRow("Analysis threads", self.threads)
        form.addRow("Hash", self.hash)
        form.addRow("MultiPV lines", self.multipv)
        form.addRow("Friend Elo estimate", self.friend_elo)
        form.addRow("Grace period", self.grace)
        form.addRow("Countdown", self.countdown)
        form.addRow("Heartbeat", self.heartbeat)
        form.addRow(self.dashboard)
        form.addRow(self.lan)
        form.addRow("Saved games folder", self.games)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _ok(self):
        if not self.pair.valid():
            self.pair.edit.setFocus()
            self.pair.edit.setStyleSheet("border: 1px solid #e53935;")
            return
        c = self.cfg
        c.server_host, c.link_port = self.host.text().strip(), self.port.value()
        c.stockfish_path, c.threads, c.hash_mb, c.multipv = self.sf.text().strip(), self.threads.value(), self.hash.value(), self.multipv.value()
        c.friend_elo_estimate = self.friend_elo.value()
        c.grace_s, c.countdown_s, c.heartbeat_ms = self.grace.value(), self.countdown.value(), self.heartbeat.value()
        c.dashboard, c.dashboard_lan, c.games_dir = self.dashboard.isChecked(), self.lan.isChecked(), self.games.text().strip()
        c.save()
        self.pair.save()
        self.accept()
