import time


class ReplayGuard:
    """Rejects HELLOs outside +-window_s or whose nonce was seen in the last ttl_s."""

    def __init__(self, window_s: float = 30, ttl_s: float = 60, clock=time.time):
        self.window_s, self.ttl_s, self.clock = window_s, ttl_s, clock
        self._seen: dict[str, float] = {}

    def check(self, ts_ms: int, nonce: str) -> bool:
        now = self.clock()
        self._seen = {n: exp for n, exp in self._seen.items() if exp > now}
        if abs(ts_ms / 1000 - now) >= self.window_s or nonce in self._seen:
            return False
        self._seen[nonce] = now + self.ttl_s
        return True
