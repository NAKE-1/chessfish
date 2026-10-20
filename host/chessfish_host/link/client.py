"""Host side of the link: dial, heartbeat, reconnect with backoff, auto-RESUME.

Runs on its own asyncio loop thread. Results go out through `emit(kind, payload)`;
the GUI turns those into Qt signals. Kinds: link_stats, state, friend_move, chat,
friend_net, event, start_ack, error, link_error.
"""
import argparse
import asyncio
import secrets
import sys
import threading
import time
import uuid
from collections import deque

import websockets
from chessfish_proto import messages as lm
from chessfish_proto.link_socket import HandshakeError, dial

BACKOFF = (0.5, 1, 2, 4, 8, 10)
ACTIVE = ("COUNTDOWN", "LIVE", "PAUSED")
EMITS = {lm.State: "state", lm.FriendMove: "friend_move", lm.Chat: "chat", lm.Net: "friend_net",
         lm.Event: "event", lm.StartAck: "start_ack", lm.Error: "error"}


class LinkStats:
    def __init__(self, hb: lm.Heartbeat):
        self.hb = hb
        self.rtts: deque[float] = deque(maxlen=30)
        self.pings: deque[list] = deque()  # [seq, t_sent, answered]
        self.last_pong = time.monotonic()

    def sent(self, seq: int):
        now = time.monotonic()
        self.pings.append([seq, now, False])
        while self.pings and now - self.pings[0][1] > 60:
            self.pings.popleft()

    def pong(self, seq: int, t_send: float):
        self.last_pong = time.monotonic()
        self.rtts.append((self.last_pong - t_send) * 1000)
        for p in self.pings:
            if p[0] == seq:
                p[2] = True

    @property
    def state(self) -> str:
        silent_ms = (time.monotonic() - self.last_pong) * 1000
        return "lost" if silent_ms >= self.hb.lost_after_ms else "degraded" if silent_ms >= self.hb.degraded_after_ms else "ok"

    def snapshot(self) -> dict:
        r = list(self.rtts)
        jitter = sum(abs(a - b) for a, b in zip(r, r[1:])) / (len(r) - 1) if len(r) > 1 else 0.0
        due = [p for p in self.pings if time.monotonic() - p[1] > self.hb.degraded_after_ms / 1000]
        loss = sum(not p[2] for p in due) / len(due) if due else 0.0
        return {"rtt_ms": r[-1] if r else None, "jitter_ms": jitter, "loss": loss, "state": self.state}


class LinkClient:
    def __init__(self, url: str, psk: bytes, emit, host_id: str = "chessfish-host"):
        self.url, self.psk, self.emit, self.host_id = url, psk, emit, host_id
        self.session: lm.Start | None = None  # what we RESUME after a reconnect
        self.phase = "IDLE"
        self.sock = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._stop = False
        self._task = None

    # ---------- thread-safe API (call from the GUI thread) ----------
    def start_thread(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="link", daemon=True).start()
        asyncio.run_coroutine_threadsafe(self._spawn(), self.loop)

    async def _spawn(self):
        self._task = asyncio.create_task(self.run())

    def stop(self):
        self._stop = True
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop).result(5)
            self.loop.call_soon_threadsafe(self.loop.stop)

    def start_session(self, start: lm.Start):
        self._call(self._start(start))

    def resume(self, session: lm.Start):
        """Resume an adjourned game (session = the START we sent originally)."""
        self.session = session
        self._call(self.send(lm.Resume(session_id=session.session_id, resume_token=session.resume_token)))

    def send_move(self, uci: str):
        self._call(self.send(lm.HostMove(uci=uci)))

    def send_chat(self, text: str):
        self._call(self.send(lm.Chat(text=text[:300])))

    def control(self, kind: str):
        self._call(self.send(lm.Control(kind=kind)))

    def _call(self, coro):
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    # ---------- loop side ----------
    async def _shutdown(self):
        if self.sock:
            await self.send(lm.Bye(reason="host closing"))
        if self._task:
            self._task.cancel()

    async def _start(self, start: lm.Start):
        self.session = start
        await self.send(start)

    async def send(self, msg) -> bool:
        if not self.sock:
            self.emit("error", {"code": "not_connected", "message": "link is down"})
            return False
        try:
            await self.sock.send(msg)
            return True
        except websockets.ConnectionClosed:
            return False

    async def run(self):
        attempt = 0
        while not self._stop:
            try:
                sock = await dial(self.url, self.psk, self.host_id)
            except HandshakeError as e:
                self.emit("link_error", {"kind": "handshake", "message": str(e)})
            except (OSError, asyncio.TimeoutError, websockets.InvalidURI, websockets.InvalidHandshake) as e:
                self.emit("link_error", {"kind": "unreachable", "message": str(e) or type(e).__name__})
            else:
                attempt = 0
                await self._serve(sock)
            self.emit("link_stats", {"rtt_ms": None, "jitter_ms": 0.0, "loss": 1.0, "state": "lost"})
            if self._stop:
                break
            await asyncio.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            attempt += 1

    async def _serve(self, sock):
        self.sock = sock
        hb = self.session.heartbeat if self.session else lm.Heartbeat()
        stats = LinkStats(hb)
        if self.session and self.phase in ACTIVE:
            await self.send(lm.Resume(session_id=self.session.session_id, resume_token=self.session.resume_token))
        tasks = [asyncio.create_task(self._reader(sock, stats)), asyncio.create_task(self._pinger(sock, stats, hb))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.sock = None
            for t in tasks:
                t.cancel()
            await sock.close()

    async def _reader(self, sock, stats: LinkStats):
        try:
            while True:
                m = await sock.recv()
                if isinstance(m, lm.Pong):
                    stats.pong(m.seq, m.t_send)
                    continue
                if isinstance(m, lm.State):
                    self.phase = m.phase
                    if m.phase == "ENDED" and self.session and m.session_id == self.session.session_id:
                        self.session = None
                kind = EMITS.get(type(m))
                if kind:
                    self.emit(kind, m)
        except Exception:
            return  # closed or bad frame -> reconnect

    async def _pinger(self, sock, stats: LinkStats, hb: lm.Heartbeat):
        seq = 0
        while True:
            seq += 1
            stats.sent(seq)
            try:
                await sock.send(lm.Ping(seq=seq, t_send=time.monotonic()))
            except Exception:
                return
            snap = stats.snapshot()
            self.emit("link_stats", snap)
            if snap["state"] == "lost":
                return
            await asyncio.sleep(hb.interval_ms / 1000)


def new_start(**kw) -> lm.Start:
    return lm.Start(session_id=str(uuid.uuid4()), resume_token=secrets.token_urlsafe(32), **kw)


# ---------- CLI harness ----------
def _cli():
    from chessfish_proto.crypto import psk_from_text
    p = argparse.ArgumentParser(description="Chessfish link test harness")
    p.add_argument("--server", required=True, help="host:port of the link (default port 6768)")
    p.add_argument("--psk", required=True)
    a = p.parse_args()
    server = a.server if ":" in a.server else a.server + ":6768"

    def emit(kind, payload):
        if kind == "link_stats":
            return print(f"\r[link] {payload['state']} rtt={payload['rtt_ms'] and round(payload['rtt_ms'], 1)}ms "
                         f"jitter={payload['jitter_ms']:.1f} loss={payload['loss']:.0%}   ", end="", flush=True)
        print(f"\n[{kind}] {payload.model_dump(exclude_defaults=True) if hasattr(payload, 'model_dump') else payload}")

    c = LinkClient(f"ws://{server}/link", psk_from_text(a.psk), emit)
    c.start_thread()
    print("commands: start [white|black|random] [min+inc] | move e2e4 | chat text | ctl pause|resume|abort|adjourn|resign|offer_draw|accept_draw | quit")
    for line in sys.stdin:
        cmd, _, rest = line.strip().partition(" ")
        if cmd == "start":
            args = rest.split()
            color = args[0] if args else "white"
            tc = None
            if len(args) > 1:
                m, i = args[1].split("+")
                tc = {"initial_s": int(m) * 60, "increment_s": int(i)}
            c.start_session(new_start(mode="human", host_color=color, time_control=tc))
        elif cmd == "move":
            c.send_move(rest)
        elif cmd == "chat":
            c.send_chat(rest)
        elif cmd == "ctl":
            c.control(rest)
        elif cmd == "quit":
            break
    c.stop()


if __name__ == "__main__":
    _cli()
