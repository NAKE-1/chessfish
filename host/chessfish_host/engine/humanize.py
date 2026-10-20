"""Human-looking think time for engine moves (pure function, see IMPLEMENTATION_PLAN 8)."""
import math
import random

UNTIMED_REMAINING_MS = 15 * 60 * 1000  # pretend a 15-minute clock when untimed


def humanized_delay(remaining_ms: int | None, increment_ms: int, move_number: int, legal_count: int,
                    top2_spread_cp: float | None, is_recapture: bool, min_ms: int, max_ms: int,
                    rng: random.Random = random) -> int:
    rem = UNTIMED_REMAINING_MS if remaining_ms is None else max(0, remaining_ms)
    base = rem / max(20, 40 - move_number / 2) + 0.6 * increment_ms
    # close top candidates = harder decision = longer think
    if top2_spread_cp is not None:
        base *= 0.6 + 0.8 * min(1.0, max(0.0, 1 - abs(top2_spread_cp) / 150))
    if legal_count <= 1:
        base *= 0.25
    if is_recapture:
        base *= 0.5
    if move_number <= 6:
        base *= 0.4
    base *= math.exp(rng.gauss(0, 0.35))  # log-normal noise
    delay = min(max(base, min_ms), max_ms)
    if remaining_ms is not None:
        delay = min(delay, 0.08 * rem)
    return int(delay)
