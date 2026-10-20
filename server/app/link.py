"""Host link on :6768. Any peer that fails the handshake is dropped silently."""
import asyncio
import json

from fastapi import FastAPI, WebSocket
from chessfish_proto import crypto
from chessfish_proto import messages as lm
from chessfish_proto.envelope import PC2SRV, SRV2PC, Opener, Sealer
from chessfish_proto.replay import ReplayGuard


class HostConn:
    def __init__(self, ws: WebSocket, sealer: Sealer | None):
        self.ws, self.sealer, self.lock = ws, sealer, asyncio.Lock()

    async def send(self, msg):
        try:
            async with self.lock:  # seal+send as one step so seq order == wire order
                await self.ws.send_bytes(self.sealer.seal(lm.dump(msg)))
        except Exception:
            pass  # a dead socket is noticed by the read loop

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass


def make_link_app(session, psk: bytes) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    guard = ReplayGuard()

    @app.websocket("/link")
    async def link(ws: WebSocket):
        await ws.accept()
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), 5))
            if not (isinstance(hello, dict) and crypto.verify_hello(psk, hello)
                    and guard.check(hello["ts"], hello["nonce_c"])):
                raise ValueError
        except Exception:
            return await HostConn(ws, None).close()
        welcome, priv = crypto.make_welcome(psk, hello)
        await ws.send_text(json.dumps(welcome))
        k_pc2srv, k_srv2pc = crypto.session_keys(priv, hello["epk_c"], psk, hello, welcome)
        opener, conn = Opener(k_pc2srv, PC2SRV), HostConn(ws, Sealer(k_srv2pc, SRV2PC))
        await session.host_connected(conn)
        try:
            while True:
                lost_s = session.cfg.heartbeat.lost_after_ms / 1000 if session.cfg else 8
                frame = await asyncio.wait_for(ws.receive_bytes(), lost_s)
                await session.on_host(conn, lm.parse(opener.open(frame)))
        except Exception:
            pass  # timeout, close, bad frame or bad message: drop the link
        finally:
            await session.host_disconnected(conn)
            await conn.close()

    return app
