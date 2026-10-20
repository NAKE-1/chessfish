import time

import pytest
from chessfish_proto import crypto, messages
from chessfish_proto.envelope import PC2SRV, SRV2PC, EnvelopeError, Opener, Sealer
from chessfish_proto.replay import ReplayGuard


def handshake(psk_c, psk_s):
    hello, priv_c = crypto.make_hello(psk_c, "pc")
    assert crypto.verify_hello(psk_s, hello)
    welcome, priv_s = crypto.make_welcome(psk_s, hello)
    assert crypto.verify_welcome(psk_c, hello, welcome)
    kc = crypto.session_keys(priv_c, welcome["epk_s"], psk_c, hello, welcome)
    ks = crypto.session_keys(priv_s, hello["epk_c"], psk_s, hello, welcome)
    return kc, ks


def test_roundtrip():
    psk = crypto.gen_psk()
    assert crypto.psk_from_text(crypto.psk_to_text(psk)) == psk
    kc, ks = handshake(psk, psk)
    assert kc == ks
    s, o = Sealer(kc[0], PC2SRV), Opener(ks[0], PC2SRV)
    assert o.open(s.seal({"a": 1})) == {"a": 1}
    s2, o2 = Sealer(ks[1], SRV2PC), Opener(kc[1], SRV2PC)
    assert o2.open(s2.seal({"b": 2})) == {"b": 2}


def test_wrong_psk_rejected():
    hello, _ = crypto.make_hello(crypto.gen_psk(), "pc")
    assert not crypto.verify_hello(crypto.gen_psk(), hello)
    psk = crypto.gen_psk()
    hello, _ = crypto.make_hello(psk, "pc")
    welcome, _ = crypto.make_welcome(crypto.gen_psk(), hello)
    assert not crypto.verify_welcome(psk, hello, welcome)


def test_tampered_rejected():
    psk = crypto.gen_psk()
    hello, _ = crypto.make_hello(psk, "pc")
    assert not crypto.verify_hello(psk, {**hello, "host_id": "evil"})
    assert not crypto.verify_hello(psk, {**hello, "mac": None})
    assert not crypto.verify_hello(psk, {"type": "HELLO"})
    kc, ks = handshake(psk, psk)
    frame = bytearray(Sealer(kc[0], PC2SRV).seal({"x": 1}))
    frame[-1] ^= 1
    with pytest.raises(EnvelopeError):
        Opener(ks[0], PC2SRV).open(bytes(frame))
    # wrong direction byte -> different nonce -> rejected
    with pytest.raises(EnvelopeError):
        Opener(ks[0], SRV2PC).open(Sealer(kc[0], PC2SRV).seal({"x": 1}))


def test_old_welcome_not_reusable():
    psk = crypto.gen_psk()
    h1, _ = crypto.make_hello(psk, "pc")
    w1, _ = crypto.make_welcome(psk, h1)
    h2, _ = crypto.make_hello(psk, "pc")
    assert not crypto.verify_welcome(psk, h2, w1)


def test_replay_and_stale():
    now = [1000.0]
    g = ReplayGuard(clock=lambda: now[0])
    assert g.check(1000_000, "n1")
    assert not g.check(1000_000, "n1")          # replayed nonce
    assert not g.check(1000_000 - 31_000, "n2")  # stale
    assert not g.check(1000_000 + 31_000, "n3")  # future
    now[0] += 61
    assert g.check(1060_000, "n1")              # expired from cache, fresh ts


def test_seq_reuse_rejected():
    psk = crypto.gen_psk()
    kc, ks = handshake(psk, psk)
    s, o = Sealer(kc[0], PC2SRV), Opener(ks[0], PC2SRV)
    f1, f2 = s.seal({"n": 1}), s.seal({"n": 2})
    o.open(f2)
    with pytest.raises(EnvelopeError):
        o.open(f1)
    with pytest.raises(EnvelopeError):
        o.open(f2)


def test_keys_differ_per_session():
    psk = crypto.gen_psk()
    assert handshake(psk, psk)[0] != handshake(psk, psk)[0]


def test_messages_parse():
    m = messages.parse({"type": "START", "session_id": "s", "resume_token": "r", "mode": "engine",
                        "host_color": "white", "time_control": {"initial_s": 600, "increment_s": 5}})
    assert isinstance(m, messages.Start) and m.heartbeat.interval_ms == 2000
    with pytest.raises(Exception):
        messages.parse({"type": "HOST_MOVE", "uci": "e2e4", "extra": 1})
    assert messages.parse(messages.dump(messages.Ping(seq=1, t_send=time.time()))).seq == 1
