import io

import chess
import chess.pgn
from chessfish_host.stats import metrics, openings
from chessfish_host.stats.pgn_writer import build_pgn
from chessfish_host.stats.record import GameRecord
from chessfish_proto.messages import Clocks, MoveRec


def test_win_pct_and_accuracy():
    assert metrics.win_pct(0) == 50
    assert 59 < metrics.win_pct(100) < 60  # lichess: +1 pawn ~ 59.1%
    assert metrics.win_pct(10000 - 3) == 100 and metrics.win_pct(-9990) == 0
    assert metrics.win_pct(-300) == 100 - metrics.win_pct(300)
    assert abs(metrics.move_accuracy(60, 60) - 100) < 0.01
    assert metrics.move_accuracy(80, 20) < 10
    assert metrics.move_accuracy(50, 60) > 99.9  # improving isn't penalised


def test_classify():
    assert metrics.classify(30, True) == "best"
    assert [metrics.classify(d, False) for d in (1, 4, 8, 15, 25)] == ["excellent", "good", "inaccuracy", "mistake", "blunder"]


def test_cp_loss_capped():
    assert metrics.cp_loss(50, -5000, chess.WHITE) == 1000
    assert metrics.cp_loss(-50, 20, chess.BLACK) == 70
    assert metrics.cp_loss(0, 100, chess.WHITE) == 0


def test_position_features():
    b = chess.Board("4k3/8/8/3n4/4P3/8/8/4K3 w - - 0 1")  # e4 pawn attacks undefended d5 knight
    assert "nd5" in metrics.hanging(b)
    assert metrics.material(b)["balance"] == -2
    assert metrics.phase(chess.Board()) == "opening"
    assert metrics.phase(b) == "endgame"


def test_probable_moves():
    b = chess.Board()
    lines = [{"cp": 30, "pv": ["e2e4", "e7e5"]}, {"cp": 25, "pv": ["d2d4"]}, {"cp": -200, "pv": ["g2g4"]}]
    ms = metrics.probable_moves(b, lines, 1500)
    assert abs(sum(m["prob"] for m in ms) - 1) < 1e-9
    assert ms[0]["san"] == "e4" and ms[0]["reply"] == "e5" and ms[0]["prob"] > ms[1]["prob"] > ms[2]["prob"]
    black = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")
    ms = metrics.probable_moves(black, [{"cp": 40, "pv": ["e7e5"]}, {"cp": -10, "pv": ["c7c5"]}], 2500)
    assert ms[1]["prob"] > ms[0]["prob"]  # lower white eval is better for black


def test_opening_lookup():
    b = chess.Board()
    for san in ["e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"]:
        b.push_san(san)
    eco, name = openings.lookup(b)
    assert eco == "B90" and "Najdorf" in name
    b.push_san("h3")  # deeper than the table: falls back to the deepest known position
    assert "Najdorf" in openings.lookup(b)[1]


def test_pgn_roundtrip():
    rec = GameRecord("s", chess.STARTING_FEN, chess.WHITE, "engine", 1800, "Bob", (300, 2))
    moves = [("f2f3", "f3"), ("e7e5", "e5"), ("g2g4", "g4"), ("d8h4", "Qh4#")]
    rec.sync([MoveRec(uci=u, san=s) for u, s in moves], Clocks(white_ms=299000, black_ms=301500))
    for p in rec.plies:
        rec.evals[p.fen_before] = {"cp": 0 if p.mover == chess.WHITE else -50, "pv": [p.uci], "depth": 20}
    rec.evals[rec.plies[2].fen_before]["pv"] = ["b1c3"]  # engine wanted Nc3, not g4
    rec.evals[rec.plies[-1].fen_before] = {"cp": -9999, "pv": ["d8h4"], "depth": 20}
    rec.result, rec.reason = "0-1", "checkmate"
    text = str(build_pgn(rec))
    g = chess.pgn.read_game(io.StringIO(text))
    assert g.headers["White"] == "Stockfish (Elo 1800)" and g.headers["Black"] == "Bob" and g.headers["Result"] == "0-1"
    assert [m.uci() for m in g.mainline_moves()] == [u for u, _ in moves]
    nodes = list(g.mainline())
    assert nodes[0].clock() == 299 and nodes[0].eval() is not None
    assert chess.pgn.NAG_BLUNDER in nodes[2].nags  # g4?? allows mate
