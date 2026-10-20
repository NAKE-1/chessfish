import asyncio
import json

import pytest
import websockets
from chessfish_proto import crypto
from chessfish_proto import messages as lm
from chessfish_proto.link_socket import HandshakeError, dial
from conftest import Friend, Host, Server, ftype, is_state


async def setup(server, name="Alice", **kw):
    friend = await Friend.join(server.friend_url, name)
    await friend.until(ftype("waiting"))
    host = await Host.connect(server.link_url, server.psk)
    start = await host.start(**kw)
    await friend.until(ftype("state"))
    return host, friend, start


async def test_fools_mate(server):
    host, friend, _ = await setup(server, host_color="white",
                                  time_control={"initial_s": 60, "increment_s": 1})
    await host.send(lm.HostMove(uci="f2f3"))
    st = await friend.until(lambda m: m["t"] == "state" and m["turn"] == "black")
    assert st["your_color"] == "black" and "e7" in st["dests"] and st["last_move"] == ["f2", "f3"]
    await friend.send(t="move", uci="e7e5")
    fmv = await host.until(lambda m: isinstance(m, lm.FriendMove))
    assert fmv.san == "e5"
    await host.send(lm.HostMove(uci="g2g4"))
    await friend.until(lambda m: m["t"] == "state" and len(m["moves_san"]) == 3)
    await friend.send(t="move", uci="d8h4")
    go = await friend.until(ftype("gameover"))
    assert go == {"t": "gameover", "result": "0-1", "reason": "checkmate"}
    st = await host.until(is_state("ENDED"))
    assert st.result == "0-1" and [m.san for m in st.moves][-1] == "Qh4#"


async def test_illegal_and_out_of_turn(server):
    host, friend, _ = await setup(server, host_color="black")
    await friend.send(t="move", uci="e2e5")
    assert (await friend.until(ftype("move_rejected")))["reason"] == "illegal move"
    await host.send(lm.HostMove(uci="e7e5"))
    ev = await host.until(lambda m: isinstance(m, lm.Event))
    assert ev.kind == "move_rejected" and ev.detail == "not your turn"
    await friend.send(t="move", uci="zz")
    await friend.send(t="move", uci="e2e9")
    assert (await friend.until(ftype("move_rejected")))["reason"] == "bad move format"


async def test_promotion(server):
    fen = "8/4P3/8/8/8/8/k7/4K3 w - - 0 1"  # friend is white, needs to promote
    host, friend, _ = await setup(server, host_color="black", start_fen=fen)
    await friend.send(t="move", uci="e7e8")
    assert (await friend.until(ftype("move_rejected")))["reason"] == "illegal move"
    await friend.send(t="move", uci="e7e8n")
    st = await host.until(lambda m: isinstance(m, lm.FriendMove))
    assert st.san == "e8=N"


async def test_flag_fall(server):
    host, friend, _ = await setup(server, host_color="white", time_control={"initial_s": 1, "increment_s": 0})
    go = await friend.until(ftype("gameover"), timeout=4)
    assert go["result"] == "0-1" and go["reason"] == "timeout"


async def test_flag_vs_insufficient_material(server):
    # friend = white with a rook, to move; host = black with a lone king. White flags -> draw.
    fen = "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"
    host, friend, _ = await setup(server, host_color="black", start_fen=fen,
                                  time_control={"initial_s": 1, "increment_s": 0})
    go = await friend.until(ftype("gameover"), timeout=4)
    assert go == {"t": "gameover", "result": "1/2-1/2", "reason": "timeout_vs_insufficient"}


async def test_resign_and_draw(server):
    host, friend, _ = await setup(server)
    await friend.send(t="resign")
    assert (await friend.until(ftype("gameover")))["result"] == "1-0"
    await host.start()
    await friend.send(t="draw_offer")
    assert (await host.until(lambda m: isinstance(m, lm.Event))).kind == "draw_offered"
    await host.send(lm.Control(kind="accept_draw"))
    assert (await friend.until(ftype("gameover")))["reason"] == "agreement"
    await host.start()
    await host.send(lm.Control(kind="offer_draw"))
    await friend.until(ftype("draw_offered"))
    await friend.send(t="draw_accept")
    assert (await friend.until(ftype("gameover")))["result"] == "1/2-1/2"


async def test_chat_both_ways(server):
    host, friend, _ = await setup(server)
    await friend.send(t="chat", text="gl hf")
    assert (await host.until(lambda m: isinstance(m, lm.Chat))).text == "gl hf"
    assert (await friend.until(ftype("chat")))["from"] == "You"
    await host.send(lm.Chat(text="you too"))
    assert await friend.until(ftype("chat")) == {"t": "chat", "from": "Opponent", "text": "you too"}


async def test_host_drop_pause_resume(server):
    host, friend, start = await setup(server, time_control={"initial_s": 60, "increment_s": 0}, grace_s=5)
    await host.sock.close()
    b = await friend.until(lambda m: m["t"] == "banner" and m["kind"] == "opponent_reconnecting")
    assert 4 <= b["seconds_left"] <= 5
    assert server.session.clocks.running is None
    host2 = Host(await dial(server.link_url, server.psk))
    await host2.send(lm.Resume(session_id=start.session_id, resume_token="wrong"))
    assert (await host2.until(lambda m: isinstance(m, lm.Error))).code == "bad_resume"
    await host2.send(lm.Resume(session_id=start.session_id, resume_token=start.resume_token))
    await host2.until(is_state("LIVE"))
    await friend.until(lambda m: m["t"] == "banner" and m["kind"] is None)
    assert server.session.clocks.running is not None


async def test_grace_expiry_adjourn_then_resume_after_restart(tmp_path):
    srv = await Server(tmp_path).start()
    host, friend, start = await setup(srv, host_color="white", grace_s=1,
                                      time_control={"initial_s": 300, "increment_s": 2})
    await host.send(lm.HostMove(uci="e2e4"))
    await friend.until(lambda m: m["t"] == "state" and m["turn"] == "black")
    await host.sock.close()
    await friend.until(lambda m: m["t"] == "banner" and m["kind"] == "adjourned", timeout=4)
    psk = srv.psk
    await srv.stop()

    srv = await Server(tmp_path, psk).start()  # container restart: adjourned game reloaded from SQLite
    try:
        assert srv.session.phase == "ADJOURNED" and srv.session.game.moves[0]["san"] == "e4"
        friend = await Friend.join(srv.friend_url)
        await friend.until(lambda m: m["t"] == "banner" and m["kind"] == "adjourned")
        host = Host(await dial(srv.link_url, psk))
        await host.send(lm.Resume(session_id=start.session_id, resume_token=start.resume_token))
        st = await host.until(is_state("LIVE"))
        assert st.moves[0].uci == "e2e4" and 295_000 < st.clocks.white_ms <= 302_000
        assert srv.session.store.load() is None
        await friend.send(t="move", uci="e7e5")
        assert (await host.until(lambda m: isinstance(m, lm.FriendMove))).uci == "e7e5"
    finally:
        await srv.stop()


async def test_friend_drop_and_rejoin(server):
    host, friend, _ = await setup(server, grace_s=5)
    await friend.ws.close()
    assert (await host.until(lambda m: isinstance(m, lm.Event))).kind == "friend_left"
    st = await host.until(is_state("PAUSED"))
    assert st.paused_reason == "friend_lost" and st.grace_left_s
    friend = await Friend.join(server.friend_url, "Alice")
    await host.until(is_state("LIVE"))


async def test_start_needs_friend(server):
    host = await Host.connect(server.link_url, server.psk)
    await host.send(lm.Start(session_id="s", resume_token="r", mode="engine", host_color="white"))
    assert (await host.until(lambda m: isinstance(m, lm.Error))).code == "no_friend"


async def test_unauthenticated_link_dropped(server):
    with pytest.raises(HandshakeError):
        await dial(server.link_url, crypto.gen_psk())
    async with websockets.connect(server.link_url) as ws:
        await ws.send("garbage")
        with pytest.raises(websockets.ConnectionClosed):
            await asyncio.wait_for(ws.recv(), 2)
    hello, _ = crypto.make_hello(server.psk, "pc")
    async with websockets.connect(server.link_url) as ws:  # replayed HELLO
        await ws.send(json.dumps(hello))
        await ws.recv()
    async with websockets.connect(server.link_url) as ws:
        await ws.send(json.dumps(hello))
        with pytest.raises(websockets.ConnectionClosed):
            await asyncio.wait_for(ws.recv(), 2)
    assert server.session.phase == "IDLE"


LEAK_WORDS = ("stockfish", "engine", "bot", "elo", "skill", "human", "mode")


def shape(v):
    if isinstance(v, dict):
        return {k: shape(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    return type(v).__name__


async def play_scripted(server, mode):
    host, friend, _ = await setup(server, mode=mode, host_color="white",
                                  engine={"elo": 1500} if mode == "engine" else None,
                                  time_control={"initial_s": 60, "increment_s": 1})
    await host.send(lm.Chat(text="hi"))
    await host.send(lm.HostMove(uci="e2e4"))
    await friend.until(lambda m: m["t"] == "state" and m["turn"] == "black")
    await friend.send(t="move", uci="e7e5")
    await friend.send(t="chat", text="hello")
    await friend.send(t="draw_offer")
    await host.send(lm.Control(kind="offer_draw"))
    await friend.until(ftype("gameover"))
    return friend.log


async def test_leak_audit(tmp_path):
    logs = {}
    for mode in ("engine", "human"):
        srv = await Server(tmp_path / mode).start()
        try:
            logs[mode] = await play_scripted(srv, mode)
        finally:
            await srv.stop()
    shapes = {mode: {m["t"]: shape(m) for m in log} for mode, log in logs.items()}
    assert shapes["engine"] == shapes["human"]
    assert [m["t"] for m in logs["engine"]] == [m["t"] for m in logs["human"]]
    for log in logs.values():
        for m in log:
            for s in _strings(m):
                assert not any(w in s.lower() for w in LEAK_WORDS), m


def _strings(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, str):
        yield v
