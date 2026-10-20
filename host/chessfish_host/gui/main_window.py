import random
import time

import chess
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QDockWidget, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QSizePolicy, QSplitter, QTabWidget, QToolBar, QVBoxLayout, QWidget)
from chessfish_proto import messages as lm
from chessfish_proto.crypto import psk_from_text

from ..config import Config, load_psk, load_session, save_session
from ..engine.humanize import humanized_delay
from ..engine.manager import AnalysisEngine, PlayEngine
from ..link.client import LinkClient, new_start
from ..stats.pgn_writer import save_pgn
from ..stats.record import GameRecord
from .board_widget import BoardWidget, EvalBar
from .dialogs import GameSetupDialog, SettingsDialog
from .panels import EnginePanel, GamePanel, NetworkPanel, ProbablePanel, TimePanel, fmt_ms

ACTIVE = ("COUNTDOWN", "LIVE", "PAUSED")
LIGHT = {"ok": "#4caf50", "degraded": "#e6c229", "lost": "#e53935"}


class LinkBridge(QObject):
    event = Signal(str, object)  # emitted from the link thread, delivered on the UI thread


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("Chessfish")
        self.resize(1500, 900)
        self.bridge = LinkBridge()
        self.bridge.event.connect(self.on_link)
        self.client: LinkClient | None = None
        self.analysis: AnalysisEngine | None = None
        self.player: PlayEngine | None = None
        self.state: lm.State | None = None
        self.state_at = time.monotonic()
        saved = load_session()
        self.session: lm.Start | None = lm.Start(**saved) if saved else None
        self.record: GameRecord | None = None
        self.board = chess.Board()
        self.snap: dict | None = None
        self.thinking_ply: int | None = None
        self.play_token = 0
        self.startup_checked = False
        self.link_error = None
        self.on_snapshot = None  # dashboard hook: called with the stats dict
        self._build()
        self._timer = QTimer(self, interval=100, timeout=self._tick)
        self._timer.start()
        self._slow = QTimer(self, interval=700, timeout=self._refresh_record)
        self._slow.start()
        self.restart_engines()
        self.restart_link()

    # ---------- UI ----------
    def _build(self):
        tb = QToolBar("Session")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.act = {}
        for key, text, fn in (("new", "New game…", self.new_game), ("pause", "Pause", lambda: self._ctl("pause")),
                              ("resume", "Resume", lambda: self._ctl("resume")), ("adjourn", "Adjourn", lambda: self._ctl("adjourn")),
                              ("resume_adj", "Resume adjourned", self.resume_adjourned), ("abort", "Abort", self.abort),
                              ("draw", "Offer draw", lambda: self._ctl("offer_draw")), ("resign", "Resign", self.resign)):
            a = QAction(text, self)
            a.triggered.connect(fn)
            tb.addAction(a)
            self.act[key] = a
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.phase_label = QLabel("–")
        self.friend_label = QLabel("Friend: –")
        self.light = QLabel("● link")
        for w in (self.phase_label, self.friend_label, self.light):
            w.setContentsMargins(10, 0, 10, 0)
            tb.addWidget(w)
        settings = QAction("Settings", self)
        settings.triggered.connect(self.open_settings)
        tb.addAction(settings)

        left = QWidget()
        lv = QVBoxLayout(left)
        self.top_row, self.top_clock = QLabel("–"), QLabel("–")
        self.bottom_row, self.bottom_clock = QLabel("–"), QLabel("–")
        for name, clock in ((self.top_row, self.top_clock), (self.bottom_row, self.bottom_clock)):
            clock.setObjectName("clock")
            name.setObjectName("player")
        top = QHBoxLayout()
        top.addWidget(self.top_row)
        top.addStretch()
        top.addWidget(self.top_clock)
        bottom = QHBoxLayout()
        bottom.addWidget(self.bottom_row)
        bottom.addStretch()
        bottom.addWidget(self.bottom_clock)
        mid = QHBoxLayout()
        self.evalbar = EvalBar()
        self.boardw = BoardWidget()
        self.boardw.move_made.connect(self.on_board_move)
        mid.addWidget(self.evalbar)
        mid.addWidget(self.boardw, 1)
        lv.addLayout(top)
        lv.addLayout(mid, 1)
        lv.addLayout(bottom)

        self.tabs = QTabWidget()
        self.engine_panel, self.probable_panel = EnginePanel(), ProbablePanel()
        self.game_panel, self.time_panel, self.net_panel = GamePanel(), TimePanel(), NetworkPanel()
        self.session_log = QPlainTextEdit(readOnly=True)
        for w, name in ((self.engine_panel, "Engine"), (self.probable_panel, "Probable moves"), (self.game_panel, "Game"),
                        (self.time_panel, "Time"), (self.net_panel, "Network"), (self.session_log, "Session log")):
            self.tabs.addTab(w, name)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setSizes([700, 800])
        self.setCentralWidget(split)

        chat = QWidget()
        cv = QVBoxLayout(chat)
        self.chat_log = QPlainTextEdit(readOnly=True)
        self.chat_in = QLineEdit(placeholderText="Message to the friend (shown as “Opponent”)…", maxLength=300)
        self.chat_in.returnPressed.connect(self.send_chat)
        cv.addWidget(self.chat_log)
        cv.addWidget(self.chat_in)
        dock = QDockWidget("Chat", self)
        dock.setWidget(chat)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [170], Qt.Vertical)
        self._update_actions()

    def log(self, text: str):
        self.session_log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")
        self.statusBar().showMessage(text, 8000)

    # ---------- engines / link ----------
    def restart_engines(self):
        if self.analysis:
            self.analysis.stop()
        if self.player:
            self.player.quit()
        self.analysis = AnalysisEngine(self.cfg.stockfish_path, self.cfg.threads, self.cfg.hash_mb, self.cfg.multipv)
        self.analysis.updated.connect(self.on_analysis)
        self.analysis.failed.connect(self.log)
        self.analysis.start()
        self.analysis.set_position(self.board)
        self.player = PlayEngine(self.cfg.stockfish_path, self.cfg.play_threads)
        self.player.move_ready.connect(self.on_engine_move)
        self.player.failed.connect(self.log)

    def restart_link(self):
        if self.client:
            self.client.stop()
            self.client = None
        psk = load_psk()
        if not (self.cfg.server_host and psk):
            self.log("Server address or pairing key missing: open Settings.")
            return
        self.startup_checked = False
        self.client = LinkClient(self.cfg.link_url, psk_from_text(psk), self.bridge.event.emit)
        self.client.start_thread()
        self.log(f"Connecting to {self.cfg.link_url}")

    def on_link(self, kind: str, p):
        if kind == "link_stats":
            self.net_panel.link_stats(p)
            self.light.setText(f"● link {p['state']}" + (f" {p['rtt_ms']:.0f} ms" if p["rtt_ms"] is not None else ""))
            self.light.setStyleSheet(f"color: {LIGHT[p['state']]}")
            if p["state"] == "ok":
                self.link_error = None
            return
        self.net_panel.frame()
        if kind == "state":
            self.on_state(p)
        elif kind == "friend_move":
            self.log(f"Friend played {p.san}")
        elif kind == "chat":
            self.chat_log.appendPlainText(f"{self.state.friend_name if self.state else 'Friend'}: {p.text}")
            self.log("Chat from friend")
        elif kind == "friend_net":
            self.net_panel.friend_net(p)
        elif kind == "event":
            self.on_event(p)
        elif kind == "start_ack":
            self.on_start_ack(p)
        elif kind == "error":
            self.log(f"Server: {p.code} {p.message}" if hasattr(p, "code") else f"Error: {p['message']}")
        elif kind == "link_error":
            if p["message"] != self.link_error:
                self.link_error = p["message"]
                self.log(f"Link: {p['message']}")
                self.net_panel.note(f"{p['kind']}: {p['message']}")

    # ---------- server state ----------
    def on_state(self, s: lm.State):
        prev, self.state, self.state_at = self.state, s, time.monotonic()
        if not self.startup_checked:
            self.startup_checked = True
            if self.session and s.session_id == self.session.session_id and s.phase in ACTIVE:
                self.log("Re-attaching to the running game")
                self.client.resume(self.session)
        board = self._board_from_state(s)
        if self.record and self.record.session_id == s.session_id:
            if prev and prev.phase == "COUNTDOWN" and s.phase == "LIVE" and not self.record.plies:
                self.record.restart_timer()
            if self.record.sync(s.moves, s.clocks):
                self.game_panel.update_record(self.record)
        changed = board.fen() != self.board.fen() or len(board.move_stack) != len(self.board.move_stack)
        self.board = board
        if changed or prev is None:
            self.boardw.set_board(board)
            self.boardw.set_arrows([])
            self.snap = None
            self.analysis.set_position(board)
            self.game_panel.update_position(board)
        host = chess.WHITE if s.host_color != "black" else chess.BLACK
        self.boardw.set_orientation(host)
        self.evalbar.orientation = host
        human = not self.session or self.session.mode == "human"
        self.boardw.set_movable(host if human and s.phase == "LIVE" and s.turn == s.host_color else None)
        if not changed and self.boardw.board.fen() != board.fen():  # rejected optimistic move
            self.boardw.set_board(board)
        self._maybe_engine_move(s, board)
        if prev is None or prev.phase != s.phase:
            self.log(f"Session {s.phase.lower()}" + (f": {s.result} {s.reason}" if s.result else ""))
            if s.phase in ("ENDED", "ADJOURNED"):
                self._save_game(s)
        self.friend_label.setText(f"Friend: {s.friend_name or '–'} ({'online' if s.friend_connected else 'offline'})")
        self._update_actions()

    def _board_from_state(self, s: lm.State) -> chess.Board:
        start = self.session.start_fen if self.session and self.session.session_id == s.session_id and self.session.start_fen else None
        try:
            b = chess.Board(start) if start else chess.Board()
            for m in s.moves:
                b.push_uci(m.uci)
            if b.fen() == s.fen:
                return b
        except ValueError:
            pass
        return chess.Board(s.fen)

    def on_start_ack(self, ack: lm.StartAck):
        self.log(f"Session {ack.session_id[:8]} acknowledged, host plays {ack.host_color}")
        if self.session and ack.session_id == self.session.session_id and (not self.record or self.record.session_id != ack.session_id):
            s = self.session
            tc = (s.time_control.initial_s, s.time_control.increment_s) if s.time_control else None
            self.record = GameRecord(s.session_id, s.start_fen or chess.STARTING_FEN,
                                     chess.WHITE if ack.host_color == "white" else chess.BLACK, s.mode,
                                     s.engine.elo if s.engine else None,
                                     (self.state.friend_name if self.state else None) or "Friend", tc)
            self.thinking_ply = None

    def _save_game(self, s: lm.State):
        if s.phase == "ENDED" and self.session and s.session_id == self.session.session_id:
            save_session(None)
            self.session = None
        if not self.record or self.record.session_id != s.session_id:
            return
        self.record.sync(s.moves, s.clocks)
        self.record.result, self.record.reason = (s.result or "*"), (s.reason or "adjourned")
        try:
            path = save_pgn(self.record, self.cfg.games_dir)
            self.log(f"Saved {path}")
        except OSError as e:
            self.log(f"Could not save PGN: {e}")

    def on_event(self, e: lm.Event):
        if e.kind == "draw_offered":
            self.chat_log.appendPlainText("— friend offers a draw —")
            self._answer_draw()
        elif e.kind == "move_rejected":
            self.log(f"Move rejected: {e.detail}")
            self.thinking_ply = None
            self.boardw.set_board(self.board)
        elif e.kind in ("friend_joined", "friend_left"):
            self.log(f"Friend {e.detail or ''} {'joined' if e.kind == 'friend_joined' else 'left'}")

    def _answer_draw(self):
        if self.session and self.session.mode == "engine":
            QTimer.singleShot(random.randint(2000, 6000), self._engine_draw_decision)
            return
        box = QMessageBox(QMessageBox.Question, "Draw offer", "Your friend offers a draw. Accept?",
                          QMessageBox.Yes | QMessageBox.No, self)
        box.finished.connect(lambda r: self._ctl("accept_draw" if r == QMessageBox.Yes else "decline_draw"))
        box.open()

    def _engine_draw_decision(self):
        s = self.state
        if not s or s.phase != "LIVE":
            return
        cp = self.snap["lines"][0]["cp"] if self.snap and self.snap["lines"] else 0
        host_cp = cp if s.host_color == "white" else -cp
        self._ctl("accept_draw" if len(s.moves) >= 30 and host_cp <= 30 else "decline_draw")

    # ---------- engine mode ----------
    def _maybe_engine_move(self, s: lm.State, board: chess.Board):
        if not (self.session and self.session.mode == "engine" and s.phase == "LIVE" and s.turn == s.host_color):
            return
        ply = len(s.moves)
        if self.thinking_ply == ply:
            return
        self.thinking_ply = ply
        self.play_token += 1
        self.player.think(board, self._engine_delay(s, board), self.play_token)

    def _engine_delay(self, s: lm.State, board: chess.Board) -> int:
        e = self.session.engine or lm.EngineCfg()
        if not e.humanize:
            return e.min_ms
        tc = self.session.time_control
        remaining = getattr(s.clocks, f"{s.host_color}_ms") if tc else None
        lines = self.snap["lines"] if self.snap and self.snap["fen"] == board.fen() else []
        spread = abs(lines[0]["cp"] - lines[1]["cp"]) if len(lines) > 1 else None
        recapture = False
        if board.move_stack and lines and lines[0]["pv"]:
            last = board.peek()
            best = chess.Move.from_uci(lines[0]["pv"][0])
            prev = board.copy()
            prev.pop()
            recapture = prev.is_capture(last) and best.to_square == last.to_square and board.is_capture(best)
        return humanized_delay(remaining, tc.increment_s * 1000 if tc else 0, board.fullmove_number,
                               board.legal_moves.count(), spread, recapture, e.min_ms, e.max_ms)

    def on_engine_move(self, uci: str, token: int):
        s = self.state
        if token != self.play_token:
            return
        if s and s.phase == "LIVE" and s.turn == s.host_color and len(s.moves) == self.thinking_ply:
            self.client.send_move(uci)
        else:
            self.thinking_ply = None  # paused meanwhile: think again when LIVE

    # ---------- analysis ----------
    def on_analysis(self, snap: dict):
        if self.record:
            self.record.on_analysis(snap)
        if snap["fen"] != self.board.fen():
            return
        self.snap = snap
        self.engine_panel.update_snap(snap)
        lines = snap["lines"]
        if lines:
            self.evalbar.set_eval(lines[0]["cp"], lines[0]["mate"])
            self.boardw.set_arrows([(l["pv"][0][:2], l["pv"][0][2:4], i) for i, l in enumerate(lines[:3]) if l["pv"]])
            host = chess.WHITE if not self.state or self.state.host_color != "black" else chess.BLACK
            friend_turn = self.board.turn != host
            engine_mode = self.session and self.session.mode == "engine"
            elo = self.cfg.friend_elo_estimate if friend_turn or not engine_mode else (self.session.engine.elo or self.cfg.elo)
            self.probable_panel.update_moves(self.board, lines, elo, friend_turn)
        if self.on_snapshot:
            self.on_snapshot(self.dashboard_payload())

    def dashboard_payload(self) -> dict:
        s = self.state
        return {"fen": self.board.fen(), "phase": s.phase if s else None, "analysis": self.snap,
                "summary": self.record.summary() if self.record else None,
                "plies": [{"san": p.san, "think_ms": p.think_ms, "clock_ms": p.clock_ms, **(self.record.ply_stats(i) or {})}
                          for i, p in enumerate(self.record.plies)] if self.record else [],
                "link": self.net_panel.labels["state"].text(), "rtt": self.net_panel.labels["rtt"].text(),
                "friend_rtt": self.net_panel.labels["friend_rtt"].text()}

    def _refresh_record(self):
        if self.record and self.tabs.currentWidget() in (self.game_panel, self.time_panel):
            self.game_panel.update_record(self.record)
            self.time_panel.update_record(self.record)

    # ---------- clocks / status ----------
    def _tick(self):
        s = self.state
        if not s:
            return
        host_col = s.host_color or "white"
        friend_col = "black" if host_col == "white" else "white"
        game = self.session or self.record
        engine = game is not None and game.mode == "engine"
        self.bottom_row.setText(f"{'Stockfish' if engine else 'You'} ({host_col})")
        self.top_row.setText(f"{s.friend_name or 'Friend'} ({friend_col})")
        for label, col in ((self.bottom_clock, host_col), (self.top_clock, friend_col)):
            ms = getattr(s.clocks, f"{col}_ms")
            if ms is not None and s.clocks.running == col:
                ms -= int((time.monotonic() - self.state_at) * 1000)
            label.setText(fmt_ms(ms))
            label.setProperty("active", s.clocks.running == col)
            label.style().polish(label)
        extra = ""
        now = time.monotonic() - self.state_at
        if s.phase == "COUNTDOWN" and s.countdown_left_s is not None:
            extra = f" {max(0, s.countdown_left_s - int(now))}s"
        elif s.phase == "PAUSED":
            extra = f" ({s.paused_reason}" + (f", adjourn in {max(0, s.grace_left_s - int(now))}s" if s.grace_left_s else "") + ")"
        elif s.phase == "ENDED" and s.result:
            extra = f" {s.result} ({s.reason})"
        self.phase_label.setText(f"{s.phase}{extra}")

    def _update_actions(self):
        ph = self.state.phase if self.state else None
        attached = self.client is not None and ph is not None
        en = {
            "new": attached and ph not in ACTIVE,
            "pause": ph == "LIVE", "resume": ph == "PAUSED" and "host_paused" in (self.state.paused_reason or ""),
            "adjourn": ph in ACTIVE, "abort": ph in ACTIVE + ("ADJOURNED",),
            "resume_adj": ph == "ADJOURNED" and self.session is not None and self.session.session_id == self.state.session_id,
            "draw": ph == "LIVE", "resign": ph in ("LIVE", "PAUSED"),
        }
        for k, v in en.items():
            self.act[k].setEnabled(bool(v))

    # ---------- actions ----------
    def _ctl(self, kind: str):
        if self.client:
            self.client.control(kind)

    def new_game(self):
        if not self.state or not self.state.friend_connected:
            QMessageBox.information(self, "New game", "Your friend isn't connected yet.\nThey need to open the website and enter a name.")
            return
        dlg = GameSetupDialog(self.cfg, self)
        if dlg.exec() != GameSetupDialog.Accepted:
            return
        dlg.apply()
        self.start_game()

    def start_game(self):
        c = self.cfg
        engine = lm.EngineCfg(elo=None if c.use_skill else c.elo, skill=c.skill if c.use_skill else None,
                              humanize=c.humanize, min_ms=c.min_ms, max_ms=c.max_ms) if c.mode == "engine" else None
        start = new_start(mode=c.mode, host_color=c.host_color,
                          time_control=None if c.untimed else {"initial_s": c.tc_initial_s, "increment_s": c.tc_increment_s},
                          engine=engine, countdown_s=c.countdown_s, grace_s=c.grace_s,
                          heartbeat={"interval_ms": c.heartbeat_ms})
        if engine:
            try:
                self.player.configure(engine.elo, engine.skill)
            except Exception as e:
                QMessageBox.critical(self, "Stockfish", f"Could not start the play engine:\n{e}")
                return
        self.session, self.record, self.thinking_ply = start, None, None
        save_session(start.model_dump(mode="json"))
        self.client.start_session(start)
        self.chat_log.appendPlainText("— new game —")

    def resume_adjourned(self):
        if self.session and self.session.mode == "engine" and self.session.engine:
            self.player.configure(self.session.engine.elo, self.session.engine.skill)
        self.client.resume(self.session)

    def abort(self):
        if QMessageBox.question(self, "Abort", "Abort this game? It will not be saved as a result.") == QMessageBox.Yes:
            self._ctl("abort")

    def resign(self):
        if QMessageBox.question(self, "Resign", "Resign this game?") == QMessageBox.Yes:
            self._ctl("resign")

    def on_board_move(self, uci: str):
        if self.client:
            self.client.send_move(uci)

    def send_chat(self):
        text = self.chat_in.text().strip()
        if text and self.client:
            self.client.send_chat(text)
            self.chat_log.appendPlainText(f"You: {text}")
            self.chat_in.clear()

    def open_settings(self):
        old = (self.cfg.stockfish_path, self.cfg.threads, self.cfg.hash_mb, self.cfg.multipv)
        old_link = (self.cfg.server_host, self.cfg.link_port, load_psk())
        if SettingsDialog(self.cfg, self).exec() != SettingsDialog.Accepted:
            return
        if old != (self.cfg.stockfish_path, self.cfg.threads, self.cfg.hash_mb, self.cfg.multipv):
            self.restart_engines()
        if old_link != (self.cfg.server_host, self.cfg.link_port, load_psk()):
            self.restart_link()
        self.settings_changed()

    def settings_changed(self):
        """Hook for the dashboard toggle (set by __main__)."""

    def closeEvent(self, e):
        if self.client:
            self.client.stop()
        if self.analysis:
            self.analysis.stop()
        if self.player:
            self.player.quit()
        super().closeEvent(e)
