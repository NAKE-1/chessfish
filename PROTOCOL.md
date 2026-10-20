# Host-link protocol (gaming PC ⇄ container)

Transport: WebSocket at `ws://<vm-tailscale-name>:6768/link` (inside Tailscale/WireGuard).
All frames after the handshake are binary AEAD envelopes. Shared code lives in `shared/chessfish_proto/`.

## 0. Pairing (one time)
- Chessfish GUI → "Pair new server" generates a random 32-byte **pairing key (PSK)**, shown as base64 text.
- Put it in the container's `.env` as `CHESSFISH_PSK=...`. Both ends now share it. Rotatable any time.

## 1. Handshake (dynamic per session)
```
PC  → SRV  HELLO  { v:1, host_id, ts, nonce_c(16B), epk_c (X25519 pub), mac }
SRV → PC   WELCOME{ v:1, ts, nonce_c (echoed), nonce_s(16B), epk_s, mac }
```
- `mac` = HMAC-SHA256(PSK, canonical JSON of all other fields) — proves both sides know the PSK without sending it.
  WELCOME echoes `nonce_c`, so an old WELCOME can't be replayed against a new HELLO.
- Replay guard: `|ts - now| < 30 s` and nonce cache for 60 s.
- Session key: `K = HKDF-SHA256(ikm = X25519(e_c, e_s), salt = PSK, info = "chessfish/v1" || nonce_c || nonce_s)`
  → two directional keys `K_pc→srv`, `K_srv→pc` (forward secrecy: ephemeral keys are discarded).

## 2. Startup packet (first encrypted frame)
Envelope: `seq (u64) || ChaCha20-Poly1305(K_dir, nonce = seq, plaintext = JSON)`; seq must strictly increase.
```json
{ "type": "START",
  "session_id": "uuid4", "resume_token": "random 32B b64",
  "mode": "engine" | "human",
  "host_color": "white" | "black" | "random",
  "time_control": { "initial_s": 600, "increment_s": 5 } | null,
  "engine": { "elo": 1800, "skill": null, "humanize": true, "min_ms": 800, "max_ms": 20000 },
  "countdown_s": 10,
  "heartbeat": { "interval_ms": 2000, "degraded_after_ms": 5000, "lost_after_ms": 8000 },
  "grace_s": 60,
  "start_fen": null,
  "friend_view": { "show_ping": true, "chat": true, "opponent_name": "Opponent" } }
```
Server replies `START_ACK {session_id, fen}`. The friend page goes from **"Waiting for game"** to a **countdown** (`countdown_s`), then the game starts.
`mode` and `engine` never leave the server toward the friend. The friend protocol is identical in both modes.

## 3. Live messages
| Dir | type | purpose |
|---|---|---|
| SRV→PC | `STATE` | fen, move list (UCI+SAN), clocks, whose turn, result |
| SRV→PC | `FRIEND_MOVE` | friend's validated move |
| PC→SRV | `HOST_MOVE` | engine or host move (server re-validates; illegal = rejected) |
| both | `CHAT` | chat line |
| both | `PING`/`PONG` | `{seq, t_send}` → RTT, jitter, loss |
| SRV→PC | `NET` | friend's browser RTT, so the host sees both legs |
| PC→SRV | `CONTROL` | pause, abort, adjourn, offer/accept draw, new game |
| both | `BYE` | clean end |

## 4. Heartbeat & drop-outs
- PING every 2 s from both sides. Link states: `ok` → (no pong 5 s) `degraded` → (8 s) `lost`.
- On `lost`: server **freezes clocks**, friend sees banner *"Host connection lost — reconnecting… 0:52"*.
- PC auto-reconnects with backoff (0.5, 1, 2, 4 … max 10 s), sends `HELLO` + `RESUME {session_id, resume_token}` (encrypted).
  Valid within grace → exact position and clocks restored, banner clears.
- Grace expires → game **adjourned**: PGN + clocks saved to SQLite, friend sees "Game adjourned". Host can resume later.
- If the **friend** drops: the server holds the game, the host sees "friend disconnected" with a timer, and the same grace rules apply.
- Everything (RTT graph, state changes, reconnect log) is visible in the Chessfish GUI; the friend sees a ping badge + banners.
