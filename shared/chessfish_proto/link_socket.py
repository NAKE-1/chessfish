"""PC side of the handshake: dial the server and get an encrypted message socket."""
import asyncio
import json

import websockets

from . import crypto
from . import messages as lm
from .envelope import PC2SRV, SRV2PC, Opener, Sealer


class HandshakeError(Exception):
    """Server dropped us during the handshake: almost always a wrong pairing key."""


class LinkSocket:
    def __init__(self, ws, sealer: Sealer, opener: Opener):
        self.ws, self.sealer, self.opener = ws, sealer, opener

    async def send(self, msg):
        await self.ws.send(self.sealer.seal(lm.dump(msg)))

    async def recv(self):
        return lm.parse(self.opener.open(await self.ws.recv()))

    async def close(self):
        await self.ws.close()


async def dial(url: str, psk: bytes, host_id: str = "chessfish-host", timeout: float = 5) -> LinkSocket:
    ws = await websockets.connect(url, open_timeout=timeout, ping_interval=None, max_size=2 ** 20)
    try:
        hello, priv = crypto.make_hello(psk, host_id)
        await ws.send(json.dumps(hello))
        welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout))
    except (websockets.ConnectionClosed, asyncio.TimeoutError, ValueError):
        await ws.close()
        raise HandshakeError("handshake rejected (wrong pairing key?)") from None
    if not crypto.verify_welcome(psk, hello, welcome):
        await ws.close()
        raise HandshakeError("server failed to prove the pairing key")
    k_pc2srv, k_srv2pc = crypto.session_keys(priv, welcome["epk_s"], psk, hello, welcome)
    return LinkSocket(ws, Sealer(k_pc2srv, PC2SRV), Opener(k_srv2pc, SRV2PC))
