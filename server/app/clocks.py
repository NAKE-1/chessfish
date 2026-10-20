import time

import chess


class Clocks:
    """Fischer clocks in ms on a monotonic timer. The server owns time."""

    def __init__(self, initial_ms: int, increment_ms: int, clock=time.monotonic):
        self.left = {chess.WHITE: initial_ms, chess.BLACK: initial_ms}
        self.inc, self.clock = increment_ms, clock
        self.running: chess.Color | None = None
        self._since = 0.0

    def _settle(self):
        if self.running is not None:
            now = self.clock()
            self.left[self.running] -= int((now - self._since) * 1000)
            self._since = now

    def start(self, color: chess.Color):
        self._settle()
        self.running, self._since = color, self.clock()

    def moved(self, mover: chess.Color):
        self._settle()
        self.left[mover] += self.inc
        self.start(not mover)

    def pause(self):
        self._settle()
        self.running = None

    def remaining(self, color: chess.Color) -> int:
        self._settle()
        return self.left[color]

    def flagged(self) -> chess.Color | None:
        if self.running is not None and self.remaining(self.running) <= 0:
            self.left[self.running] = 0
            return self.running
        return None

    def snapshot(self) -> dict:
        self._settle()
        return {"white_ms": max(0, self.left[chess.WHITE]), "black_ms": max(0, self.left[chess.BLACK]),
                "running": None if self.running is None else ("white" if self.running else "black")}
