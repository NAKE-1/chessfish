"""Pairing key + HELLO/WELCOME handshake (see PROTOCOL.md section 1)."""
import base64
import hashlib
import hmac
import json
import os
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

VERSION = 1


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def gen_psk() -> bytes:
    return os.urandom(32)


def psk_to_text(psk: bytes) -> str:
    return b64e(psk)


def psk_from_text(text: str) -> bytes:
    psk = b64d(text.strip())
    if len(psk) != 32:
        raise ValueError("pairing key must be 32 bytes")
    return psk


def canonical(fields: dict) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()


def _mac(psk: bytes, msg: dict) -> str:
    body = {k: v for k, v in msg.items() if k != "mac"}
    return b64e(hmac.new(psk, canonical(body), hashlib.sha256).digest())


def _check_mac(psk: bytes, msg: dict) -> bool:
    mac = msg.get("mac")
    return isinstance(mac, str) and hmac.compare_digest(mac, _mac(psk, msg))


def _pub(priv: X25519PrivateKey) -> str:
    return b64e(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def now_ms() -> int:
    return int(time.time() * 1000)


def make_hello(psk: bytes, host_id: str) -> tuple[dict, X25519PrivateKey]:
    priv = X25519PrivateKey.generate()
    msg = {"type": "HELLO", "v": VERSION, "host_id": host_id, "ts": now_ms(),
           "nonce_c": b64e(os.urandom(16)), "epk_c": _pub(priv)}
    msg["mac"] = _mac(psk, msg)
    return msg, priv


def verify_hello(psk: bytes, msg: dict) -> bool:
    """MAC + shape only; the caller applies ReplayGuard on (ts, nonce_c)."""
    try:
        return (msg.get("type") == "HELLO" and msg.get("v") == VERSION and isinstance(msg.get("ts"), int)
                and len(b64d(msg["nonce_c"])) == 16 and len(b64d(msg["epk_c"])) == 32
                and _check_mac(psk, msg))
    except (KeyError, TypeError, ValueError):
        return False


def make_welcome(psk: bytes, hello: dict) -> tuple[dict, X25519PrivateKey]:
    # nonce_c is echoed so an old WELCOME can't be replayed against a new HELLO.
    priv = X25519PrivateKey.generate()
    msg = {"type": "WELCOME", "v": VERSION, "ts": now_ms(), "nonce_c": hello["nonce_c"],
           "nonce_s": b64e(os.urandom(16)), "epk_s": _pub(priv)}
    msg["mac"] = _mac(psk, msg)
    return msg, priv


def verify_welcome(psk: bytes, hello: dict, msg: dict) -> bool:
    try:
        return (msg.get("type") == "WELCOME" and msg.get("v") == VERSION
                and msg.get("nonce_c") == hello["nonce_c"]
                and len(b64d(msg["nonce_s"])) == 16 and len(b64d(msg["epk_s"])) == 32
                and _check_mac(psk, msg))
    except (KeyError, TypeError, ValueError):
        return False


def derive_keys(shared_secret: bytes, psk: bytes, nonce_c: bytes, nonce_s: bytes) -> tuple[bytes, bytes]:
    okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=psk,
               info=b"chessfish/v1" + nonce_c + nonce_s).derive(shared_secret)
    return okm[:32], okm[32:]  # (k_pc2srv, k_srv2pc)


def session_keys(priv: X25519PrivateKey, peer_epk: str, psk: bytes, hello: dict, welcome: dict) -> tuple[bytes, bytes]:
    shared = priv.exchange(X25519PublicKey.from_public_bytes(b64d(peer_epk)))
    return derive_keys(shared, psk, b64d(hello["nonce_c"]), b64d(welcome["nonce_s"]))
