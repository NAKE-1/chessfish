import re
import time
from pathlib import Path

import chess
import chess.pgn

from . import metrics, openings
from .record import GameRecord

NAG = {"inaccuracy": chess.pgn.NAG_DUBIOUS_MOVE, "mistake": chess.pgn.NAG_MISTAKE, "blunder": chess.pgn.NAG_BLUNDER}


def _clk(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def build_pgn(rec: GameRecord) -> chess.pgn.Game:
    g = chess.pgn.Game()
    host = f"Stockfish (Elo {rec.elo})" if rec.mode == "engine" and rec.elo else "Stockfish" if rec.mode == "engine" else "Host"
    white, black = (host, rec.friend_name) if rec.host_color == chess.WHITE else (rec.friend_name, host)
    g.headers.update({
        "Event": "Chessfish", "Site": "Chessfish", "Date": time.strftime("%Y.%m.%d", time.localtime(rec.started)),
        "Round": "-", "White": white, "Black": black, "Result": rec.result,
        "TimeControl": f"{rec.time_control[0]}+{rec.time_control[1]}" if rec.time_control else "-",
    })
    if rec.reason:
        g.headers["Termination"] = rec.reason
    if rec.start_fen != chess.STARTING_FEN:
        g.setup(chess.Board(rec.start_fen))
    board = rec.board()
    if (op := openings.lookup(board)) is not None:
        g.headers["ECO"], g.headers["Opening"] = op
    node = g
    for i, p in enumerate(rec.plies):
        node = node.add_variation(chess.Move.from_uci(p.uci))
        parts = []
        if (s := rec.ply_stats(i)) is not None:
            cp = s["cp_after"]
            plies_to_mate = metrics.MATE_CP - abs(cp)
            if abs(cp) < 9000:
                parts.append(f"[%eval {cp / 100:.2f}]")
            elif plies_to_mate > 0:  # no eval on the mating move itself
                parts.append(f"[%eval #{'-' if cp < 0 else ''}{(plies_to_mate + 1) // 2}]")
            if s["class"] in NAG:
                node.nags.add(NAG[s["class"]])
        if p.clock_ms is not None:
            parts.append(f"[%clk {_clk(p.clock_ms)}]")
        node.comment = " ".join(parts)
    return g


def save_pgn(rec: GameRecord, games_dir: str) -> Path:
    name = re.sub(r"[^\w\-]+", "_", rec.friend_name or "friend")[:24]
    path = Path(games_dir) / f"{time.strftime('%Y-%m-%d_%H%M', time.localtime(rec.started))}_{name}.pgn"
    Path(games_dir).mkdir(parents=True, exist_ok=True)
    path.write_text(str(build_pgn(rec)) + "\n", encoding="utf-8")
    return path
