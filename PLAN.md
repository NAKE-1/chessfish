# Chessfish — plan

## Topology
```
 Gaming PC (tailnet)                     Proxmox VM (tailnet, Tailscale on VM)          Friend (outside tailnet)
 ┌──────────────────────────┐            ┌───────────────────────────────────┐         ┌──────────────────┐
 │ Chessfish (PySide6)      │  WS :6768  │ docker: chessfish-server          │ :6767   │ Browser          │
 │  ├ Stockfish (UCI)       │ ─────────► │  ├ FastAPI + python-chess (rules) │ ◄────── │ (Tailscale share │
 │  ├ stats engine          │ encrypted  │  ├ website (board, chat, ping)    │ HTTP/WS │  of the VM node) │
 │  └ local dashboard       │ handshake  │  └ SQLite (sessions, adjourned)   │         └──────────────────┘
 │    127.0.0.1:6769        │            │ (other VM services untouched)               │
 └──────────────────────────┘            └───────────────────────────────────┘
```
- The friend reaches the VM through the existing **Tailscale node share**. Tailscale settings are never touched.
- The host link (6768) is gated only by the PSK + X25519 handshake (see `PROTOCOL.md`). Unauthenticated connections are dropped.

## Components
### 1. chessfish-server (Docker container on the VM)
- Python 3.12, **FastAPI + uvicorn**, **python-chess** = authoritative game state (legal moves, check/mate,
  stalemate, 50-move, threefold, insufficient material, promotion, castling, en passant).
- Server-side clocks (monotonic), increment, flag fall, pause on link loss.
- Friend WebSocket `/play`, host link WebSocket `/link` (port 6768), static site on 6767.
- SQLite volume: only the adjourned game waiting to resume. No history is kept on the server (PGNs are saved on the gaming PC).
- Sends the friend the **legal destinations map** for each position, so the browser needs no rules engine.

### 2. Friend website (served by the container)
- **chessground** board (lichess's board UI): drag/click, legal-move dots, last-move + check highlight,
  premove off by default, promotion picker, flip board.
- Sounds: move, capture, check, castle, game start/end, low time (own/CC0 audio files).
- Move list (SAN), clocks, result banner, resign, offer/accept draw.
- Chat panel; connection badge (RTT ms) + banners ("waiting for host", "host reconnecting 0:52", "adjourned").
- Friend enters a display name. The opponent is always shown as **"Opponent"**: no engine label and nothing that reveals engine vs host.
- Idle state: **"Waiting for game"**, then a countdown when the host starts a session. No rematch or request buttons.
- No engine output, no settings, no other controls.

### 3. Chessfish host app (gaming PC, PySide6)
Threads: **UI thread** · **Engine worker** (python-chess `SimpleEngine` / async UCI to Stockfish, MultiPV) ·
**Link worker** (asyncio websockets + crypto) · **Stats worker** (derives metrics) · **Dashboard server** (FastAPI, optional).
Qt signals pass data between threads, so the UI never blocks.
- **Settings:** server address (Tailscale IP / MagicDNS) + port, PSK pairing, Stockfish path, Threads, Hash,
  MultiPV, analysis depth/time, mode, colour, time control, engine Elo/Skill/movetime, grace period, heartbeat interval.
- **Session panel:** Start / Pause / Adjourn / Abort, link state light, RTT/jitter/loss graph, reconnect log.
- **Board:** live game; in human mode the host plays here.
- **Stats panels** ("super verbose"):
  - Engine: eval (cp/mate), **WDL %**, depth/seldepth, nodes, NPS, hashfull, tbhits, time, PV per MultiPV line.
  - **Probable moves:** top-N candidates with a probability spread (softmax over MultiPV scores) for both sides,
    predicted friend reply, expected eval swing per candidate.
  - Game: eval graph, per-move centipawn loss, accuracy %, classification (best/excellent/good/inaccuracy/mistake/blunder),
    opening (ECO + name), phase, material balance, piece-square/mobility counts, hanging pieces, threats, king safety.
  - Time: think time per move, clock usage curves.
  - Network: both legs' RTT (PC⇄server, server⇄friend), state history, packet counters, session info.
- **Local web dashboard** (optional toggle): same stats in a browser at `http://127.0.0.1:6769`
  (LAN bind optional, off by default). Never reachable by the friend.

### 4. shared/chessfish_proto
Message schemas (pydantic), envelope encode/decode, handshake, used by both server and host.

## Repo layout
```
chessfish/
  CLAUDE.md PLAN.md PROTOCOL.md DECISIONS.md OPEN_QUESTIONS.md
  shared/chessfish_proto/        # crypto, schemas, envelope
  server/                        # container
    app/ (main.py, game.py, clocks.py, link.py, play.py, store.py)
    web/ (index.html, app.js, board css, sounds/)
    Dockerfile
  host/                          # gaming PC
    chessfish_host/ (main.py, gui/, engine/, link/, stats/, dashboard/, config.py)
    pyproject.toml, chessfish.spec (PyInstaller)
  deploy/
    docker-compose.yml, .env.example, tailscale-acl-snippet.hujson
  tests/
```

## Build phases
1. **Proto + crypto**: handshake, envelopes, replay guard, unit tests.
2. **Server core**: game state, clocks, friend WS, host link, SQLite; tests with a fake host.
3. **Website**: board, dots, sounds, moves, clocks, chat, status banners, promotion.
4. **Host link client**: connect/heartbeat/reconnect/resume; CLI harness before the GUI.
5. **Chessfish GUI**: settings, session control, board, engine worker (engine and human modes).
6. **Stats engine + panels**: all metrics above, graphs (pyqtgraph).
7. **Local web dashboard**.
8. **Installer**: PyInstaller build + Inno Setup → `ChessfishSetup.exe` (see "Installer" below).
9. **Deploy + E2E**: `docker compose up -d` on the VM (ports 6767/6768), install on the gaming PC, end-to-end test incl. pulling the network cable.

## Fair play: the friend can't tell engine from human
- The server sends the friend exactly the same messages in both modes; there is no engine name or mode field in the friend protocol.
- **Humanized think-time** for engine moves: the delay is drawn from a distribution scaled by remaining clock, increment,
  move number and position complexity (legal-move count, MultiPV eval spread). Forced moves and recaptures come faster, and openings come quicker.
- Strength is capped by UCI_Elo/Skill, so play quality looks human-level.
- Chat reaches the host in both modes, so the host can answer either way.

## Installer (gaming PC, handed off to another PC)
Output: `ChessfishSetup-<ver>.exe`, built with **PyInstaller (onedir)** and wrapped by **Inno Setup** (`installer/chessfish.iss`).
- **Bundles:** the Chessfish app (Python embedded, no Python needed on the target), **Stockfish builds for several CPU levels**
  (avx512 / bmi2 / avx2 / sse41-popcnt / generic x86-64), the Stockfish GPLv3 license + source link, opening names data, and sounds.
- **Install:** shortcuts in the Start menu and on the desktop (optional), plus an uninstaller. **No firewall rules are needed**, because Chessfish only dials out
  (the dashboard is localhost only).
- **First-run wizard:**
  1. Detect CPU features → pick the matching Stockfish → run `bench` to verify it works.
  2. Detect cores/RAM → defaults `Threads = cores − 1`, `Hash = min(25 % RAM, 4096 MB)` (editable).
  3. Server address + link port (default 6768) → **Test connection** (does the handshake, shows RTT).
  4. Pairing key: generate one (shown with a copy button for the container `.env`) or paste an existing one.
     Stored with Windows DPAPI (keyring), never in plain text.
- **Install location:** defaults to the folder the setup .exe is run from (`{src}\Chessfish`). It can be changed, and a per-user (no UAC)
  or all-users choice is offered.
- **Portable data:** `config\config.toml`, `logs\` and `games\` (PGNs with eval/grade/clock annotations) live inside the install dir.
  If it isn't writable, they fall back to `%APPDATA%\Chessfish`.
- **Docker files:** the wizard writes `docker\docker-compose.yml` + `docker\.env` (pairing key filled in) to copy to the VM.
- **Build script** `installer/build.ps1`: downloads the pinned Stockfish release, verifies checksums, runs PyInstaller, runs the Inno Setup compiler.

## Docker side
One command on the VM installs or updates the server, building it straight from GitHub:
```
curl -fsSL https://raw.githubusercontent.com/NAKE-1/chessfish/main/deploy/install.sh | sh -s -- <pairing-key>
```
It writes `~/chessfish/docker-compose.yml` + `.env` (`CHESSFISH_PSK`), publishes 6767/6768, data in volume `chessfish-data`.
Nothing on the VM's Tailscale is changed.
