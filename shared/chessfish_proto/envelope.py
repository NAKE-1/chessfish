"""AEAD frames: seq:u64be || ChaCha20Poly1305(key).encrypt(nonce, json, aad=seq)."""
import json
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

PC2SRV = 0x01
SRV2PC = 0x02


class EnvelopeError(Exception):
    pass


def _nonce(direction: int, seq_bytes: bytes) -> bytes:
    return bytes([direction, 0, 0, 0]) + seq_bytes


class Sealer:
    def __init__(self, key: bytes, direction: int):
        self._aead, self._dir, self._seq = ChaCha20Poly1305(key), direction, 0

    def seal(self, obj: dict) -> bytes:
        self._seq += 1
        sb = struct.pack(">Q", self._seq)
        return sb + self._aead.encrypt(_nonce(self._dir, sb), json.dumps(obj).encode(), sb)


class Opener:
    def __init__(self, key: bytes, direction: int):
        self._aead, self._dir, self._last = ChaCha20Poly1305(key), direction, 0

    def open(self, frame: bytes) -> dict:
        if len(frame) < 8 + 16:
            raise EnvelopeError("short frame")
        sb = frame[:8]
        seq = struct.unpack(">Q", sb)[0]
        if seq <= self._last:
            raise EnvelopeError("replayed seq")
        try:
            plain = self._aead.decrypt(_nonce(self._dir, sb), frame[8:], sb)
        except InvalidTag:
            raise EnvelopeError("bad tag") from None
        self._last = seq
        return json.loads(plain)
