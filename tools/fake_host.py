"""Dev tool: plays random legal moves as the host so the website can be tested without the GUI.

python tools/fake_host.py --server 127.0.0.1:6768 --psk <key> [--color black] [--tc 5+3] [--delay 1.5]
Waits for a friend, starts a game, answers chat with "ok", starts a new game 5 s after each game ends.
"""
import argparse
import asyncio
import random
import secrets
import sys
import uuid
from pathlib import Path

import chess

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
from chessfish_proto import messages as lm  # noqa: E402
from chessfish_proto.crypto import psk_from_text  # noqa: E402
from chessfish_proto.link_socket import dial  # noqa: E402


async def main(a):
    sock = await dial(f"ws://{a.server}/link", psk_from_text(a.psk))
    print("linked")
    initial, inc = (int(x) for x in a.tc.split("+")) if a.tc != "none" else (None, None)
    pending = None

    async def start_later(d):
        await asyncio.sleep(d)
        await sock.send(lm.Start(session_id=str(uuid.uuid4()), resume_token=secrets.token_urlsafe(32),
                                 mode="engine", host_color=a.color, countdown_s=a.countdown,
                                 time_control={"initial_s": initial * 60, "increment_s": inc} if initial else None))

    async def pinger():
        seq = 0
        while True:
            await asyncio.sleep(2)
            seq += 1
            await sock.send(lm.Ping(seq=seq, t_send=0))

    asyncio.create_task(pinger())
    while True:
        m = await sock.recv()
        if not isinstance(m, (lm.Pong, lm.Net)):
            print(m.type, m.model_dump(exclude_defaults=True))
        if isinstance(m, lm.Event) and m.kind == "friend_joined" and pending is None:
            pending = asyncio.create_task(start_later(1))
        elif isinstance(m, lm.Event) and m.kind == "draw_offered":
            await sock.send(lm.Control(kind="decline_draw"))
        elif isinstance(m, lm.Chat):
            await sock.send(lm.Chat(text="ok"))
        elif isinstance(m, lm.Error) and m.code == "no_friend":
            pending = None
        elif isinstance(m, lm.State):
            if m.phase == "ENDED" and pending and pending.done():
                pending = asyncio.create_task(start_later(5))
            if m.phase == "LIVE" and m.turn == m.host_color:
                board = chess.Board(m.fen)
                move = random.choice(list(board.legal_moves))
                await asyncio.sleep(a.delay)
                await sock.send(lm.HostMove(uci=move.uci()))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="127.0.0.1:6768")
    p.add_argument("--psk", required=True)
    p.add_argument("--color", default="random", choices=["white", "black", "random"])
    p.add_argument("--tc", default="5+3", help="minutes+increment, or 'none'")
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--countdown", type=int, default=10)
    asyncio.run(main(p.parse_args()))
