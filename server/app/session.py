"""Session state machine: IDLE -> COUNTDOWN -> LIVE <-> PAUSED -> ADJOURNED | ENDED.

Nothing here branches on `mode`: the friend sees the same message flow in engine and human games.
"""
import asyncio
import math
import random
import secrets
import time
from collections import deque

import chess
from chessfish_proto import friend_messages as fm
from chessfish_proto import messages as lm

from .clocks import Clocks
from .game import Game, IllegalMove, color_name

ACTIVE = ("COUNTDOWN", "LIVE", "PAUSED")
LOST = ("host_lost", "friend_lost")


class Session:
    def __init__(self, store, clock=time.monotonic):
        self.store, self.clock = store, clock
        self.phase = "IDLE"
        self.host = None
        self.host_attached = False
        self.friend = None
        self.friend_name: str | None = None
        self.cfg: lm.Start | None = None
        self.game: Game | None = None
        self.clocks: Clocks | None = None
        self.host_color = chess.WHITE
        self.pause_reasons: set[str] = set()
        self.grace_end: float | None = None
        self.countdown_end: float | None = None
        self.draw_offer: str | None = None  # "host" | "friend"
        self.result: tuple[str, str] | None = None
        self._chat_times: deque[float] = deque(maxlen=5)
        if (adj := store.load()) is not None:
            self._load_adjourned(adj)

    # ---------- views ----------
    def host_state(self) -> lm.State:
        g = self.game
        now = self.clock()
        return lm.State(
            session_id=self.cfg.session_id if self.cfg else None, phase=self.phase,
            fen=g.board.fen() if g else chess.STARTING_FEN, moves=g.moves if g else [],
            clocks=self.clocks.snapshot() if self.clocks else {},
            turn=g.turn if g else "white", host_color=color_name(self.host_color) if g else None,
            result=self.result[0] if self.result else None, reason=self.result[1] if self.result else None,
            friend_name=self.friend_name, friend_connected=self.friend is not None,
            paused_reason=",".join(sorted(self.pause_reasons)) or None,
            grace_left_s=math.ceil(self.grace_end - now) if self.grace_end else None,
            countdown_left_s=math.ceil(self.countdown_end - now) if self.phase == "COUNTDOWN" else None)

    def friend_view(self) -> list[dict]:
        if self.phase == "IDLE" or self.game is None:
            return [fm.out(fm.Waiting())]
        if self.phase == "COUNTDOWN":
            return [fm.out(fm.Countdown(seconds=max(0, math.ceil(self.countdown_end - self.clock()))))]
        g, me = self.game, not self.host_color
        state = fm.out(fm.FState(
            fen=g.board.fen(), dests=g.legal_dests() if self.phase == "LIVE" and g.board.turn == me else {},
            last_move=g.last_move, check=g.board.is_check(), turn=g.turn, your_color=color_name(me),
            moves_san=[m["san"] for m in g.moves],
            clocks=self.clocks.snapshot() if self.clocks else {"white_ms": None, "black_ms": None, "running": None}))
        if self.phase == "ENDED":
            return [state, fm.out(fm.GameOver(result=self.result[0], reason=self.result[1]))]
        if self.phase == "ADJOURNED":
            return [state, fm.out(fm.Banner(kind="adjourned"))]
        if "host_lost" in self.pause_reasons:
            banner = fm.Banner(kind="opponent_reconnecting", seconds_left=math.ceil(self.grace_end - self.clock()))
        elif "host_paused" in self.pause_reasons:
            banner = fm.Banner(kind="paused")
        else:
            banner = fm.Banner(kind=None)
        return [state, fm.out(banner)]

    async def push(self):
        await self.to_host(self.host_state())
        for m in self.friend_view():
            await self.to_friend(m)

    async def to_host(self, msg):
        if self.host:
            await self.host.send(msg)

    async def to_friend(self, obj: dict):
        if self.friend:
            await self.friend.send(obj)

    # ---------- host link ----------
    async def host_connected(self, conn):
        old, self.host, self.host_attached = self.host, conn, False
        if old:
            await old.close()
        await self._lose("host_lost")
        await self.to_host(lm.Net(friend_connected=self.friend is not None))
        await self.to_host(self.host_state())

    async def host_disconnected(self, conn):
        if self.host is conn:
            self.host, self.host_attached = None, False
            await self._lose("host_lost")

    async def on_host(self, conn, msg):
        if conn is not self.host:
            return
        if isinstance(msg, lm.Ping):
            return await conn.send(lm.Pong(seq=msg.seq, t_send=msg.t_send))
        if isinstance(msg, lm.Start):
            return await self._start(msg)
        if isinstance(msg, lm.Resume):
            return await self._resume(msg)
        if isinstance(msg, lm.Control) and msg.kind == "abort":
            if self.phase in ACTIVE + ("ADJOURNED",):
                await self._end("*", "aborted")
            return
        if not self.host_attached:
            return await conn.send(lm.Error(code="not_attached", message="send START or RESUME first"))
        if isinstance(msg, lm.HostMove):
            if err := await self._move(self.host_color, msg.uci):
                await conn.send(lm.Event(kind="move_rejected", detail=err))
        elif isinstance(msg, lm.Chat):
            await self.to_friend(fm.out(fm.ChatOut(**{"from": "Opponent"}, text=msg.text)))
        elif isinstance(msg, lm.Control):
            await self._host_control(msg.kind)

    async def _start(self, msg: lm.Start):
        if self.phase in ACTIVE:
            return await self.to_host(lm.Error(code="session_active"))
        if self.friend is None:
            return await self.to_host(lm.Error(code="no_friend", message="friend is not connected"))
        try:
            game = Game(msg.start_fen)
        except ValueError:
            return await self.to_host(lm.Error(code="bad_fen"))
        hc = msg.host_color if msg.host_color != "random" else random.choice(["white", "black"])
        self.cfg, self.game, self.host_color = msg, game, chess.WHITE if hc == "white" else chess.BLACK
        tc = msg.time_control
        self.clocks = Clocks(tc.initial_s * 1000, tc.increment_s * 1000, self.clock) if tc else None
        self.result, self.draw_offer, self.pause_reasons, self.grace_end = None, None, set(), None
        self.store.clear()
        self.host_attached = True
        await self._countdown()

    async def _resume(self, msg: lm.Resume):
        ok = (self.cfg and self.phase in ACTIVE + ("ADJOURNED",) and msg.session_id == self.cfg.session_id
              and secrets.compare_digest(msg.resume_token, self.cfg.resume_token))
        if not ok:
            return await self.to_host(lm.Error(code="bad_resume"))
        self.host_attached = True
        if self.phase == "ADJOURNED":
            self.store.clear()
            await self._countdown()
        else:
            await self.to_host(self._ack())
            await self._recover("host_lost")

    def _ack(self):
        return lm.StartAck(session_id=self.cfg.session_id, fen=self.game.board.fen(),
                           host_color=color_name(self.host_color))

    async def _countdown(self):
        self.phase, self.countdown_end = "COUNTDOWN", self.clock() + self.cfg.countdown_s
        await self.to_host(self._ack())
        await self.push()

    async def _host_control(self, kind: str):
        if kind == "pause" and self.phase in ("LIVE", "PAUSED"):
            await self._pause("host_paused")
        elif kind == "resume":
            await self._recover("host_paused")
        elif kind == "adjourn" and self.phase in ACTIVE:
            await self._adjourn()
        elif kind == "resign" and self.phase in ("LIVE", "PAUSED"):
            await self._end("0-1" if self.host_color == chess.WHITE else "1-0", "resign")
        elif self.phase == "LIVE" and kind in ("offer_draw", "accept_draw"):
            if self.draw_offer == "friend":
                await self._end("1/2-1/2", "agreement")
            elif kind == "offer_draw":
                self.draw_offer = "host"
                await self.to_friend(fm.out(fm.DrawOffered()))
        elif kind == "decline_draw" and self.draw_offer == "friend":
            self.draw_offer = None

    # ---------- friend ----------
    async def friend_connected(self, conn, name: str):
        old, self.friend, self.friend_name = self.friend, conn, name
        if old:
            await old.close(4001)  # page shows "open in another tab" and stops reconnecting
        await self.to_host(lm.Event(kind="friend_joined", detail=name))
        await self.to_host(lm.Net(friend_connected=True))
        if self.phase == "PAUSED":
            await self._recover("friend_lost")
        else:
            await self.push()

    async def friend_disconnected(self, conn):
        if self.friend is conn:
            self.friend = None
            await self.to_host(lm.Event(kind="friend_left", detail=self.friend_name))
            await self.to_host(lm.Net(friend_connected=False))
            await self._lose("friend_lost")

    async def on_friend(self, conn, msg):
        if conn is not self.friend:
            return
        me = not self.host_color
        if isinstance(msg, fm.PingIn):
            await conn.send(fm.out(fm.PongOut(id=msg.id)))
            if msg.rtt is not None:
                await self.to_host(lm.Net(friend_rtt_ms=msg.rtt, friend_connected=True))
        elif isinstance(msg, fm.Move):
            if err := await self._move(me, msg.uci):
                await conn.send(fm.out(fm.MoveRejected(reason=err)))
                await conn.send(self.friend_view()[0])  # snap the board back
        elif isinstance(msg, fm.ChatIn):
            now = self.clock()
            if len(self._chat_times) == 5 and now - self._chat_times[0] < 10:
                return  # rate limit: 5 lines / 10 s
            self._chat_times.append(now)
            await self.to_host(lm.Chat(text=msg.text))
            await conn.send(fm.out(fm.ChatOut(**{"from": "You"}, text=msg.text)))
        elif isinstance(msg, fm.Resign) and self.phase in ("LIVE", "PAUSED"):
            await self._end("0-1" if me == chess.WHITE else "1-0", "resign")
        elif isinstance(msg, (fm.DrawOffer, fm.DrawAccept)) and self.phase == "LIVE":
            if self.draw_offer == "host":
                await self._end("1/2-1/2", "agreement")
            elif isinstance(msg, fm.DrawOffer):
                self.draw_offer = "friend"
                await self.to_host(lm.Event(kind="draw_offered"))

    # ---------- shared transitions ----------
    async def _move(self, color: chess.Color, uci: str) -> str | None:
        if self.phase != "LIVE":
            return "game is not running"
        if self.game.board.turn != color:
            return "not your turn"
        if self.clocks and (flagged := self.clocks.flagged()) is not None:
            await self._end(*self.game.flag_result(flagged))
            return "time is up"
        try:
            san = self.game.apply_uci(uci)
        except IllegalMove as e:
            return str(e)
        if self.clocks:
            self.clocks.moved(color)
        self.draw_offer = None
        if color != self.host_color:
            await self.to_host(lm.FriendMove(uci=uci, san=san))
        if o := self.game.outcome():
            await self._end(*o)
        else:
            await self.push()
        return None

    async def _pause(self, reason: str):
        if self.phase == "LIVE":
            self.phase = "PAUSED"
            if self.clocks:
                self.clocks.pause()
        self.pause_reasons.add(reason)
        if reason in LOST and self.grace_end is None:
            self.grace_end = self.clock() + self.cfg.grace_s
        await self.push()

    async def _lose(self, reason: str):
        # COUNTDOWN ignores drops: _go_live checks who is present when it ends.
        if self.phase in ("LIVE", "PAUSED"):
            await self._pause(reason)

    async def _recover(self, reason: str):
        self.pause_reasons.discard(reason)
        if not self.pause_reasons & set(LOST):
            self.grace_end = None
        if self.phase == "PAUSED" and not self.pause_reasons:
            self.phase = "LIVE"
            if self.clocks:
                self.clocks.start(self.game.board.turn)
        await self.push()

    async def _go_live(self):
        self.phase, self.countdown_end = "LIVE", None
        missing = [r for r, gone in (("host_lost", not self.host_attached), ("friend_lost", self.friend is None)) if gone]
        if missing:
            for r in missing:
                await self._pause(r)
            return
        if self.clocks:
            self.clocks.start(self.game.board.turn)
        await self.push()

    async def _end(self, result: str, reason: str):
        self.phase, self.result = "ENDED", (result, reason)
        self.pause_reasons, self.grace_end, self.countdown_end = set(), None, None
        if self.clocks:
            self.clocks.pause()
        self.store.clear()
        await self.push()

    async def _adjourn(self):
        if self.clocks:
            self.clocks.pause()
        self.phase, self.pause_reasons, self.grace_end, self.countdown_end = "ADJOURNED", set(), None, None
        self.store.save({
            "cfg": self.cfg.model_dump(mode="json"), "host_color": color_name(self.host_color),
            "start_fen": self.game.start_fen, "moves": [m["uci"] for m in self.game.moves],
            "clocks": self.clocks.snapshot() if self.clocks else None, "friend_name": self.friend_name})
        await self.push()

    def _load_adjourned(self, d: dict):
        self.cfg = lm.Start(**d["cfg"])
        self.host_color = chess.WHITE if d["host_color"] == "white" else chess.BLACK
        self.game = Game(d["start_fen"], d["moves"])
        if d["clocks"]:
            tc = self.cfg.time_control
            self.clocks = Clocks(0, tc.increment_s * 1000, self.clock)
            self.clocks.left = {chess.WHITE: d["clocks"]["white_ms"], chess.BLACK: d["clocks"]["black_ms"]}
        self.friend_name, self.phase = d["friend_name"], "ADJOURNED"

    # ---------- timers ----------
    async def tick(self):
        now = self.clock()
        if self.phase == "COUNTDOWN" and now >= self.countdown_end:
            await self._go_live()
        elif self.phase == "PAUSED" and self.grace_end and now >= self.grace_end:
            await self._adjourn()
        elif self.phase == "LIVE" and self.clocks and (flagged := self.clocks.flagged()) is not None:
            await self._end(*self.game.flag_result(flagged))

    async def run(self, interval: float = 0.1):
        while True:
            await asyncio.sleep(interval)
            await self.tick()

