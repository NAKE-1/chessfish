"""One process, two servers: friend site :6767 and host link :6768."""
import asyncio
import os
import sys
from pathlib import Path

import uvicorn
from chessfish_proto.crypto import psk_from_text

from .link import make_link_app
from .play import make_friend_app
from .session import Session
from .store import Store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def build(psk: bytes, data_dir: str, web_dir: str = str(WEB_DIR)):
    session = Session(Store(data_dir))
    return session, make_friend_app(session, web_dir), make_link_app(session, psk)


async def serve(friend_port: int = 6767, link_port: int = 6768):
    try:
        psk = psk_from_text(os.environ["CHESSFISH_PSK"])
    except (KeyError, ValueError):
        sys.exit("CHESSFISH_PSK missing or invalid (32-byte urlsafe base64). Refusing to start.")
    session, friend_app, link_app = build(psk, os.environ.get("CHESSFISH_DATA", "/data"))
    servers = [uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info"))
               for app, port in ((friend_app, friend_port), (link_app, link_port))]
    ticker = asyncio.create_task(session.run())
    try:
        # first server to stop (Ctrl-C / SIGTERM) stops the other
        tasks = [asyncio.create_task(s.serve()) for s in servers]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for s in servers:
            s.should_exit = True
        await asyncio.gather(*tasks)
    finally:
        ticker.cancel()


if __name__ == "__main__":
    asyncio.run(serve())
