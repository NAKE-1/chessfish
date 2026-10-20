"""Friend site on :6767: static page + /play WebSocket."""
import asyncio
import json

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from chessfish_proto import friend_messages as fm

FRIEND_IDLE_S = 15  # browser pings every 2 s


class FriendConn:
    def __init__(self, ws: WebSocket):
        self.ws, self.lock = ws, asyncio.Lock()

    async def send(self, obj: dict):
        try:
            async with self.lock:
                await self.ws.send_text(json.dumps(obj))
        except Exception:
            pass

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass


def make_friend_app(session, web_dir: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.websocket("/play")
    async def play(ws: WebSocket):
        await ws.accept()
        conn = FriendConn(ws)
        try:
            hello = fm.parse_in(json.loads(await asyncio.wait_for(ws.receive_text(), 10)))
            if not isinstance(hello, fm.Hello) or not hello.name.strip():
                return
            await session.friend_connected(conn, hello.name.strip())
            while True:
                raw = await asyncio.wait_for(ws.receive_text(), FRIEND_IDLE_S)
                try:
                    msg = fm.parse_in(json.loads(raw))
                except ValueError:  # bad JSON or failed validation
                    continue
                await session.on_friend(conn, msg)
        except Exception:
            pass
        finally:
            await session.friend_disconnected(conn)
            await conn.close()

    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
