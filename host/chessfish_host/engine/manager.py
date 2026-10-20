"""Two Stockfish processes, each driven from its own thread; results reach the GUI as Qt signals.

- AnalysisEngine: full strength, infinite MultiPV analysis restarted on every new position, emits <= 10 Hz.
- PlayEngine: strength-limited, plays engine-mode moves after a humanized delay.
"""
import threading
import time

import chess
import chess.engine
from PySide6.QtCore import QObject, Signal

from ..stats.metrics import MATE_CP


def score_cp(score: chess.engine.PovScore) -> int:
    """White-POV centipawns, mates mapped to +-(10000 - plies)."""
    return score.white().score(mate_score=MATE_CP)


def line_from_info(board: chess.Board, info: dict) -> dict:
    pv = info.get("pv", [])
    wdl = info.get("wdl")
    w = wdl.white() if wdl else None
    score = info["score"]
    return {
        "multipv": info.get("multipv", 1), "cp": score_cp(score),
        "mate": score.white().mate(), "wdl": (w.wins, w.draws, w.losses) if w else None,
        "depth": info.get("depth"), "seldepth": info.get("seldepth"), "nodes": info.get("nodes"),
        "nps": info.get("nps"), "hashfull": info.get("hashfull"), "tbhits": info.get("tbhits"),
        "time": info.get("time"), "pv": [m.uci() for m in pv],
        "pv_san": board.variation_san(pv[:12]) if pv else "",
    }


class AnalysisEngine(QObject):
    updated = Signal(object)  # {"fen", "lines": [line...]}
    failed = Signal(str)

    def __init__(self, path: str, threads: int, hash_mb: int, multipv: int):
        super().__init__()
        self.path, self.threads, self.hash_mb, self.multipv = path, threads, hash_mb, multipv
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending: chess.Board | None = None
        self._an = None
        self._quit = False

    def start(self):
        threading.Thread(target=self._run, name="analysis", daemon=True).start()

    def set_position(self, board: chess.Board):
        with self._lock:
            self._pending = board.copy()
            if self._an:
                self._an.stop()
        self._wake.set()

    def stop(self):
        self._quit = True
        with self._lock:
            if self._an:
                self._an.stop()
        self._wake.set()

    def _run(self):
        try:
            engine = chess.engine.SimpleEngine.popen_uci(self.path)
            engine.configure({"Threads": self.threads, "Hash": self.hash_mb, "UCI_ShowWDL": True})
        except Exception as e:
            return self.failed.emit(f"analysis engine failed to start: {e}")
        try:
            while not self._quit:
                self._wake.wait()
                self._wake.clear()
                with self._lock:
                    board, self._pending = self._pending, None
                if board is None or self._quit:
                    continue
                if board.is_game_over():
                    self.updated.emit({"fen": board.fen(), "lines": []})
                    continue
                self._analyse(engine, board)
        finally:
            engine.quit()

    def _analyse(self, engine, board: chess.Board):
        lines: dict[int, dict] = {}
        last = 0.0
        threat = None
        if not board.is_check():  # threat = best move for the other side if we passed
            nb = board.copy(stack=False)
            nb.turn, nb.ep_square = not nb.turn, None  # "pass" without a null move in the history
            info = engine.analyse(nb, chess.engine.Limit(depth=10))
            if info.get("pv"):
                threat = {"uci": info["pv"][0].uci(), "san": nb.san(info["pv"][0]), "cp": score_cp(info["score"])}
        n = min(self.multipv, board.legal_moves.count())
        with engine.analysis(board, multipv=n, info=chess.engine.INFO_ALL) as an:
            with self._lock:
                self._an = an
                if self._pending is not None or self._quit:
                    an.stop()
            for info in an:
                if "score" in info and "pv" in info:
                    lines[info.get("multipv", 1)] = line_from_info(board, info)
                    if time.monotonic() - last >= 0.1:
                        last = time.monotonic()
                        self.updated.emit({"fen": board.fen(), "threat": threat,
                                           "lines": [lines[k] for k in sorted(lines)]})
            with self._lock:
                self._an = None
        if lines:
            self.updated.emit({"fen": board.fen(), "threat": threat, "lines": [lines[k] for k in sorted(lines)]})


class PlayEngine(QObject):
    move_ready = Signal(str, int)  # uci, token
    failed = Signal(str)

    def __init__(self, path: str, threads: int):
        super().__init__()
        self.path, self.threads = path, threads
        self._engine = None
        self._game = None
        self._lock = threading.Lock()

    def configure(self, elo: int | None, skill: int | None):
        with self._lock:
            if self._engine is None:
                self._engine = chess.engine.SimpleEngine.popen_uci(self.path)
            opts = {"Threads": self.threads, "Hash": 64}
            if skill is not None:
                opts.update({"UCI_LimitStrength": False, "Skill Level": skill})
            else:
                opts.update({"UCI_LimitStrength": True, "UCI_Elo": max(1320, min(3190, elo or 1500)), "Skill Level": 20})
            self._engine.configure(opts)
            self._game = object()  # new game key -> python-chess sends ucinewgame

    def think(self, board: chess.Board, delay_ms: int, token: int):
        threading.Thread(target=self._think, args=(board.copy(), delay_ms, token), daemon=True).start()

    def _think(self, board: chess.Board, delay_ms: int, token: int):
        t0 = time.monotonic()
        try:
            with self._lock:
                search_s = max(0.05, min(delay_ms / 1000 * 0.7, 5.0))
                move = self._engine.play(board, chess.engine.Limit(time=search_s), game=self._game).move
        except Exception as e:
            return self.failed.emit(f"play engine error: {e}")
        time.sleep(max(0.0, delay_ms / 1000 - (time.monotonic() - t0)))
        self.move_ready.emit(move.uci(), token)

    def quit(self):
        with self._lock:
            if self._engine:
                self._engine.quit()
                self._engine = None
