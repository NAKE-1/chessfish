import asyncio
import json
import secrets
import uuid

import pytest
import uvicorn
import websockets
from app.main import build
from chessfish_proto import crypto
from chessfish_proto import messages as lm
from chessfish_proto.link_socket import dial


class Server:
    def __init__(self, tmp_path, psk=None):
        self.psk = psk or crypto.gen_psk()
        self.tmp_path = tmp_path

    async def start(self):
        self.session, friend_app, link_app = build(self.psk, str(self.tmp_path))
        self.servers = [uvicorn.Server(uvicorn.Config(a, host="127.0.0.1", port=0, log_level="warning"))
                        for a in (friend_app, link_app)]
        for s in self.servers:
            s.install_signal_handlers = lambda: None
        self.tasks = [asyncio.create_task(s.serve()) for s in self.servers]
        self.tasks.append(asyncio.create_task(self.session.run(0.02)))
        while not all(s.started for s in self.servers):
            await asyncio.sleep(0.01)
        fp, lp = (s.servers[0].sockets[0].getsockname()[1] for s in self.servers)
        self.friend_url, self.link_url = f"ws://127.0.0.1:{fp}/play", f"ws://127.0.0.1:{lp}/link"
        self.http = f"http://127.0.0.1:{fp}"
        return self

    async def stop(self):
        for s in self.servers:
            s.should_exit = True
        self.tasks[-1].cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.session.store.db.close()


@pytest.fixture
async def server(tmp_path):
    s = await Server(tmp_path).start()
    yield s
    await s.stop()


class Friend:
    def __init__(self, ws):
        self.ws, self.log = ws, []

    @classmethod
    async def join(cls, url, name="Alice"):
        f = cls(await websockets.connect(url, ping_interval=None))
        await f.ws.send(json.dumps({"t": "hello", "name": name}))
        return f

    async def send(self, **obj):
        await self.ws.send(json.dumps(obj))

    async def until(self, pred, timeout=3):
        async with asyncio.timeout(timeout):
            while True:
                m = json.loads(await self.ws.recv())
                self.log.append(m)
                if pred(m):
                    return m


class Host:
    def __init__(self, sock):
        self.sock, self.log = sock, []

    @classmethod
    async def connect(cls, url, psk):
        return cls(await dial(url, psk))

    async def send(self, msg):
        await self.sock.send(msg)

    async def until(self, pred, timeout=3):
        async with asyncio.timeout(timeout):
            while True:
                m = await self.sock.recv()
                self.log.append(m)
                if pred(m):
                    return m

    async def start(self, **kw):
        cfg = dict(session_id=str(uuid.uuid4()), resume_token=secrets.token_urlsafe(32), mode="engine",
                   host_color="white", countdown_s=0, grace_s=5)
        cfg.update(kw)
        start = lm.Start(**cfg)
        await self.send(start)
        await self.until(lambda m: isinstance(m, lm.State) and m.phase == "LIVE")
        return start


def is_state(phase=None):
    return lambda m: isinstance(m, lm.State) and (phase is None or m.phase == phase)


def ftype(t):
    return lambda m: m["t"] == t
