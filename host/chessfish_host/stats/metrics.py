"""Chess statistics. Win%/accuracy formulas are lichess's (lila: WinPercent.scala, AccuracyPercent.scala)."""
import math

import chess

MATE_CP = 10000  # engine mate scores are +-(MATE_CP - plies)
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
# win% drop thresholds (mover's POV); "best" = played the engine's top move
CLASS_THRESHOLDS = (("excellent", 2), ("good", 5), ("inaccuracy", 10), ("mistake", 20))
NAGS = {"inaccuracy": "?!", "mistake": "?", "blunder": "??"}


def win_pct(cp: float) -> float:
    """White-POV win% from white-POV cp. Mate scores (|cp| >= 9000) map to 0/100."""
    if cp >= 9000:
        return 100.0
    if cp <= -9000:
        return 0.0
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(wp_before: float, wp_after: float) -> float:
    """Both in the mover's POV."""
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * max(0.0, wp_before - wp_after)) - 3.1669))


def classify(wp_drop: float, is_best: bool) -> str:
    if is_best:
        return "best"
    for name, limit in CLASS_THRESHOLDS:
        if wp_drop <= limit:
            return name
    return "blunder"


def mover_pov(cp_white: float, mover: chess.Color) -> float:
    return cp_white if mover == chess.WHITE else -cp_white


def cp_loss(cp_before_white: float, cp_after_white: float, mover: chess.Color) -> float:
    before = max(-1000, min(1000, mover_pov(cp_before_white, mover)))
    after = max(-1000, min(1000, mover_pov(cp_after_white, mover)))
    return max(0.0, min(1000.0, before - after))


def probable_moves(board: chess.Board, lines: list[dict], elo: int) -> list[dict]:
    """Softmax over MultiPV evals (side-to-move POV); temperature grows as Elo drops.
    Each entry also carries the predicted reply (2nd PV move) and the eval swing vs the best line."""
    lines = [l for l in lines if l["pv"]]
    if not lines:
        return []
    t = 40 + (3200 - elo) / 10
    scores = [mover_pov(max(-2000, min(2000, l["cp"])), board.turn) for l in lines]
    top = max(scores)
    exps = [math.exp((s - top) / t) for s in scores]
    z = sum(exps)
    out = []
    for l, s, e in zip(lines, scores, exps):
        move = chess.Move.from_uci(l["pv"][0])
        reply = None
        if len(l["pv"]) > 1:
            after = board.copy(stack=False)
            after.push(move)
            reply = after.san(chess.Move.from_uci(l["pv"][1]))
        out.append({"uci": l["pv"][0], "san": board.san(move), "prob": e / z, "cp": s, "swing": s - top, "reply": reply})
    return out


def material(board: chess.Board) -> dict:
    w = sum(VALUES[p.piece_type] for p in board.piece_map().values() if p.color == chess.WHITE)
    b = sum(VALUES[p.piece_type] for p in board.piece_map().values() if p.color == chess.BLACK)
    return {"white": w, "black": b, "balance": w - b}


def mobility(board: chess.Board) -> dict:
    out = {}
    for color in (chess.WHITE, chess.BLACK):
        b = board.copy(stack=False)
        b.turn = color
        out["white" if color else "black"] = b.legal_moves.count() if b.is_valid() or b.turn == board.turn else \
            sum(1 for _ in b.pseudo_legal_moves)
    return out


def hanging(board: chess.Board) -> list[str]:
    """Pieces attacked and undefended, or attacked by a cheaper piece. e.g. ["Nf3", "pe5"]."""
    out = []
    for sq, p in board.piece_map().items():
        if p.piece_type == chess.KING:
            continue
        attackers = board.attackers(not p.color, sq)
        if not attackers:
            continue
        # a king can only take an undefended piece, so it never counts as the "cheaper attacker"
        cheapest = min(100 if board.piece_type_at(a) == chess.KING else VALUES[board.piece_type_at(a)] for a in attackers)
        if not board.attackers(p.color, sq) or cheapest < VALUES[p.piece_type]:
            out.append(p.symbol() + chess.square_name(sq))
    return out


def king_zone_attacks(board: chess.Board) -> dict:
    out = {}
    for color in (chess.WHITE, chess.BLACK):
        k = board.king(color)
        if k is None:
            continue
        zone = chess.SquareSet(chess.BB_KING_ATTACKS[k]) | chess.SquareSet.from_square(k)
        attackers = set()
        for sq in zone:
            attackers |= set(board.attackers(not color, sq))
        out["white" if color else "black"] = len(attackers)
    return out


def phase(board: chess.Board) -> str:
    npm = sum(VALUES[p.piece_type] for p in board.piece_map().values() if p.piece_type not in (chess.PAWN, chess.KING))
    if board.fullmove_number <= 10 and npm >= 56:
        return "opening"
    return "endgame" if npm <= 26 else "middlegame"
