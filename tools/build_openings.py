"""Turn lichess chess-openings TSVs (CC0, github.com/lichess-org/chess-openings) into eco<TAB>name<TAB>epd.

PYTHONIOENCODING=utf-8 python tools/build_openings.py a.tsv b.tsv c.tsv d.tsv e.tsv > host/chessfish_host/data/openings.tsv
"""
import io
import sys

import chess.pgn

print("eco\tname\tepd")
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            eco, name, pgn = line.rstrip("\n").split("\t")
            game = chess.pgn.read_game(io.StringIO(pgn))
            print(f"{eco}\t{name}\t{game.end().board().epd()}")
