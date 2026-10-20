import chess


class IllegalMove(Exception):
    pass


def color_name(c: chess.Color) -> str:
    return "white" if c == chess.WHITE else "black"


class Game:
    """Authoritative rules. Claimable draws (50-move, threefold) are auto-claimed."""

    def __init__(self, fen: str | None = None, moves: list[str] = ()):
        self.board = chess.Board(fen) if fen else chess.Board()
        if not self.board.is_valid():
            raise ValueError("invalid start position")
        self.start_fen = self.board.fen()
        self.moves: list[dict] = []
        for uci in moves:
            self.apply_uci(uci)

    @property
    def turn(self) -> str:
        return color_name(self.board.turn)

    def legal_dests(self) -> dict[str, list[str]]:
        d: dict[str, set[str]] = {}
        for m in self.board.legal_moves:  # promotions repeat the same to-square
            d.setdefault(chess.square_name(m.from_square), set()).add(chess.square_name(m.to_square))
        return {k: sorted(v) for k, v in d.items()}

    def apply_uci(self, uci: str) -> str:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise IllegalMove("bad move format") from None
        if move not in self.board.legal_moves:  # promotion without a piece is not legal
            raise IllegalMove("illegal move")
        san = self.board.san(move)
        self.board.push(move)
        self.moves.append({"uci": uci, "san": san})
        return san

    @property
    def last_move(self) -> list[str] | None:
        if not self.board.move_stack:
            return None
        m = self.board.peek()
        return [chess.square_name(m.from_square), chess.square_name(m.to_square)]

    def outcome(self) -> tuple[str, str] | None:
        o = self.board.outcome(claim_draw=True)
        return (o.result(), o.termination.name.lower()) if o else None

    def flag_result(self, flagged: chess.Color) -> tuple[str, str]:
        if self.board.has_insufficient_material(not flagged):
            return "1/2-1/2", "timeout_vs_insufficient"
        return ("0-1" if flagged == chess.WHITE else "1-0"), "timeout"
