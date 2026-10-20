"""Per-game record: every ply with think time, clock and the analysis evals before/after it."""
import time
from dataclasses import dataclass, field

import chess

from . import metrics


@dataclass
class Ply:
    uci: str
    san: str
    mover: chess.Color
    fen_before: str
    fen_after: str
    think_ms: int
    clock_ms: int | None  # mover's clock after the move


@dataclass
class GameRecord:
    session_id: str
    start_fen: str
    host_color: chess.Color
    mode: str
    elo: int | None
    friend_name: str
    time_control: tuple[int, int] | None  # (initial_s, increment_s)
    started: float = field(default_factory=time.time)
    plies: list[Ply] = field(default_factory=list)
    evals: dict[str, dict] = field(default_factory=dict)  # fen -> best line (deepest seen)
    result: str = "*"
    reason: str = ""
    _last_t: float = field(default_factory=time.monotonic)

    def board(self) -> chess.Board:
        b = chess.Board(self.start_fen)
        for p in self.plies:
            b.push_uci(p.uci)
        return b

    def sync(self, moves: list, clocks) -> bool:
        """Append plies we haven't seen yet from a STATE. Returns True if anything was added."""
        if len(moves) <= len(self.plies):
            return False
        b = self.board()
        now = time.monotonic()
        for m in moves[len(self.plies):]:
            before = b.fen()
            mover = b.turn
            b.push_uci(m.uci)
            clock = getattr(clocks, "white_ms" if mover == chess.WHITE else "black_ms", None)
            self.plies.append(Ply(m.uci, m.san, mover, before, b.fen(), int((now - self._last_t) * 1000), clock))
            self._last_t = now
        return True

    def restart_timer(self):
        self._last_t = time.monotonic()

    def on_analysis(self, snap: dict):
        if snap["lines"]:
            best = snap["lines"][0]
            old = self.evals.get(snap["fen"])
            if old is None or (best.get("depth") or 0) >= (old.get("depth") or 0):
                self.evals[snap["fen"]] = best

    def eval_of(self, fen: str) -> dict | None:
        if (e := self.evals.get(fen)) is not None:
            return e
        b = chess.Board(fen)
        if b.is_checkmate():
            return {"cp": -metrics.MATE_CP if b.turn == chess.WHITE else metrics.MATE_CP, "pv": [], "depth": 0}
        if b.is_stalemate() or b.is_insufficient_material():
            return {"cp": 0, "pv": [], "depth": 0}
        return None

    def ply_stats(self, i: int) -> dict | None:
        p = self.plies[i]
        before, after = self.eval_of(p.fen_before), self.eval_of(p.fen_after)
        if before is None or after is None:
            return None
        wp_b = metrics.win_pct(metrics.mover_pov(before["cp"], p.mover))
        wp_a = metrics.win_pct(metrics.mover_pov(after["cp"], p.mover))
        is_best = bool(before["pv"]) and before["pv"][0] == p.uci
        return {"cp_after": after["cp"], "accuracy": metrics.move_accuracy(wp_b, wp_a),
                "class": metrics.classify(wp_b - wp_a, is_best), "cp_loss": metrics.cp_loss(before["cp"], after["cp"], p.mover)}

    def summary(self) -> dict:
        out = {}
        for color in (chess.WHITE, chess.BLACK):
            stats = [s for i, p in enumerate(self.plies) if p.mover == color and (s := self.ply_stats(i))]
            counts = {}
            for s in stats:
                counts[s["class"]] = counts.get(s["class"], 0) + 1
            out["white" if color else "black"] = {
                "accuracy": sum(s["accuracy"] for s in stats) / len(stats) if stats else None,
                "acpl": sum(s["cp_loss"] for s in stats) / len(stats) if stats else None,
                "counts": counts}
        return out
