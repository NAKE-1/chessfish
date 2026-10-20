"""Synthesize the website's sound effects as small mono WAVs (no licensing issues).

Usage: python tools/make_sounds.py [out_dir]   (default: server/web/sounds)
"""
import math
import random
import struct
import sys
import wave
from pathlib import Path

RATE = 22050


def tone(freq, ms, vol=0.5, decay=8.0, noise=0.0):
    n = int(RATE * ms / 1000)
    rnd = random.Random(freq)
    out = []
    for i in range(n):
        t = i / RATE
        env = math.exp(-decay * t / (ms / 1000)) * min(1.0, i / 40)  # 40-sample attack avoids clicks
        s = math.sin(2 * math.pi * freq * t) * (1 - noise) + (rnd.uniform(-1, 1) * noise)
        out.append(vol * env * s)
    return out


def silence(ms):
    return [0.0] * int(RATE * ms / 1000)


def write(path: Path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))


SOUNDS = {
    "move": lambda: tone(180, 90, 0.7, 10, noise=0.35),
    "capture": lambda: tone(140, 60, 0.8, 9, noise=0.6) + tone(220, 110, 0.6, 10, noise=0.4),
    "check": lambda: tone(880, 120, 0.35, 5) + tone(660, 160, 0.35, 5),
    "castle": lambda: tone(180, 80, 0.7, 10, noise=0.35) + silence(60) + tone(200, 80, 0.7, 10, noise=0.35),
    "start": lambda: tone(523, 120, 0.3, 4) + tone(659, 120, 0.3, 4) + tone(784, 260, 0.3, 4),
    "end": lambda: tone(784, 140, 0.3, 4) + tone(659, 140, 0.3, 4) + tone(523, 320, 0.3, 4),
    "lowtime": lambda: tone(1000, 70, 0.3, 3) + silence(80) + tone(1000, 70, 0.3, 3),
}

if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent.parent / "server" / "web" / "sounds")
    out.mkdir(parents=True, exist_ok=True)
    for name, make in SOUNDS.items():
        write(out / f"{name}.wav", make())
        print("wrote", out / f"{name}.wav")
