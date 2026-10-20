import asyncio

from chessfish_host.link.client import LinkClient, new_start
from chessfish_proto import messages as lm
from conftest import Friend, ftype


class Collector:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()

    def emit(self, kind, payload):
        self.q.put_nowait((kind, payload))

    async def until(self, pred, timeout=5):
        async with asyncio.timeout(timeout):
            while True:
                kind, p = await self.q.get()
                if pred(kind, p):
                    return p


def state(phase):
    return lambda k, p: k == "state" and p.phase == phase


async def test_client_reconnects_and_resumes(server):
    friend = await Friend.join(server.friend_url)
    await friend.until(ftype("waiting"))
    col = Collector()
    client = LinkClient(server.link_url, server.psk, col.emit)
    task = asyncio.create_task(client.run())
    try:
        stats = await col.until(lambda k, p: k == "link_stats" and p["state"] == "ok")
        await client._start(new_start(mode="human", host_color="white", countdown_s=0, grace_s=10,
                                      time_control={"initial_s": 60, "increment_s": 0},
                                      heartbeat={"interval_ms": 200}))
        await col.until(state("LIVE"))
        await client.send(lm.HostMove(uci="e2e4"))
        await friend.until(lambda m: m["t"] == "state" and m["turn"] == "black")

        # network drop: server side kills the link
        await server.session.host.close()
        await friend.until(lambda m: m["t"] == "banner" and m["kind"] == "opponent_reconnecting")
        await col.until(lambda k, p: k == "start_ack")  # auto RESUME accepted
        st = await col.until(state("LIVE"))
        assert [m.uci for m in st.moves] == ["e2e4"]
        await friend.until(lambda m: m["t"] == "banner" and m["kind"] is None)

        await friend.send(t="move", uci="e7e5")
        assert (await col.until(lambda k, p: k == "friend_move")).san == "e5"
        s = await col.until(lambda k, p: k == "link_stats" and p["rtt_ms"] is not None)
        assert s["rtt_ms"] < 500 and s["state"] == "ok"
    finally:
        client._stop = True
        task.cancel()


async def test_client_wrong_key_reports_handshake(server):
    from chessfish_proto.crypto import gen_psk
    col = Collector()
    client = LinkClient(server.link_url, gen_psk(), col.emit)
    task = asyncio.create_task(client.run())
    try:
        err = await col.until(lambda k, p: k == "link_error")
        assert err["kind"] == "handshake"
    finally:
        client._stop = True
        task.cancel()


async def test_client_unreachable():
    col = Collector()
    client = LinkClient("ws://127.0.0.1:1/link", b"x" * 32, col.emit)
    task = asyncio.create_task(client.run())
    try:
        assert (await col.until(lambda k, p: k == "link_error"))["kind"] == "unreachable"
    finally:
        client._stop = True
        task.cancel()
