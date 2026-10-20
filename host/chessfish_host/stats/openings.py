import chess

from ..paths import RES_DIR

_TABLE: dict[str, tuple[str, str]] | None = None


def _table() -> dict[str, tuple[str, str]]:
    global _TABLE
    if _TABLE is None:
        _TABLE = {}
        path = RES_DIR / "data" / "openings.tsv"
        with open(path, encoding="utf-8") as f:
            next(f)
            for line in f:
                eco, name, epd = line.rstrip("\n").split("\t")
                _TABLE[epd] = (eco, name)
    return _TABLE


def lookup(board: chess.Board) -> tuple[str, str] | None:
    """Deepest known position reached in the game -> (ECO, name). Handles transpositions."""
    b = board.copy()
    table = _table()
    while True:
        if (hit := table.get(b.epd())) is not None:
            return hit
        if not b.move_stack:
            return None
        b.pop()
