# Chessfish — implementation & install plan (handoff to Claude on the gaming PC)

You are the Claude Code instance on the **gaming PC**. You will build all of Chessfish here (host app, server image,
website, installer), test it, and produce `ChessfishSetup.exe` plus the Docker files for the VM.

## 0. Read first and follow always
1. Read `CLAUDE.md`, `PLAN.md`, `PROTOCOL.md`, `DECISIONS.md` and `OPEN_QUESTIONS.md` (all in this zip). `DECISIONS.md` wins on conflicts.
2. **Hard rules (from the user):**
   - **NEVER touch, change or propose changes to Tailscale settings/ACLs.** Not even to "test". The tailnet is fixed.
   - **The friend must never be able to tell whether the opponent is Stockfish or the host.** Nothing on the friend's
     wire, page, timing or chat may leak the mode. They only see "Opponent".
   - **Ask the user (AskUserQuestion) whenever unsure.** Record every answer in `DECISIONS.md`, and add new unknowns to `OPEN_QUESTIONS.md`.
   - Keep all project memory in `.md` files in the repo root.
3. Copy these `.md` files into the repo root you create, and keep them updated as you go.
4. Work phase by phase. Each phase ends with its **acceptance checks passing**. Commit after each phase (`git init` first).

## 1. Fixed parameters
| Thing | Value |
|---|---|
| Friend website + friend WS | container port **6767** (`/`, `/play`) |
| Host link WS | container port **6768** (`/link`) |
| Local dashboard | `127.0.0.1:6769` (off by default) |
| Heartbeat | 2000 ms ping, degraded 5000 ms, lost 8000 ms |
| Grace before adjourn | 60 s |
| Pre-game countdown | 10 s |
| Game history | PGNs **on the gaming PC only**; server keeps only an adjourned game |
| Install dir default | the folder the setup .exe is run from (`{src}\Chessfish`), changeable, per-user or all-users |

## 2. Dev prerequisites (gaming PC)
Install with winget if missing (ask the user before installing anything):
```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
winget install -e --id JRSoftware.InnoSetup
# optional, only to test the server image locally:
winget install -e --id Docker.DockerDesktop
```
Python env: `py -3.12 -m venv .venv` and `.\.venv\Scripts\Activate.ps1`. Pin all deps in `requirements*.txt` (exact `==` versions).

Main deps:
- **shared:** `cryptography`, `pydantic>=2`, `websockets`
- **server:** `fastapi`, `uvicorn[standard]`, `python-chess`
- **host:** `PySide6`, `pyqtgraph`, `python-chess`, `py-cpuinfo`, `psutil`, `keyring`, `tomli-w`, `fastapi`, `uvicorn`
- **dev:** `pytest`, `pytest-asyncio`, `httpx`, `pyinstaller`

## 3. Repo layout to create
```
chessfish/
  *.md                                  # memory files from the zip
  shared/chessfish_proto/__init__.py
      crypto.py  envelope.py  messages.py  replay.py  friend_messages.py
  server/
      app/main.py  game.py  clocks.py  session.py  link.py  play.py  store.py  config.py
      web/index.html  app.js  style.css  vendor/chessground/  sounds/*.mp3|ogg  pieces/
      Dockerfile  requirements.txt
  host/
      chessfish_host/__main__.py  config.py  paths.py
          gui/ (main_window.py, board_widget.py, panels/*.py, wizard.py, theme.qss)
          engine/ (manager.py, play_engine.py, analysis_engine.py, humanize.py, cpu_select.py)
          link/ (client.py)
          stats/ (metrics.py, classify.py, openings.py, pgn_writer.py)
          dashboard/ (server.py, static/)
          data/ (openings/*.tsv)
      requirements.txt
  installer/ build.ps1  chessfish.iss  chessfish.spec  stockfish.lock.json  templates/(docker-compose.yml.tmpl, env.tmpl)
  tools/ make_sounds.py
  tests/ (test_proto.py, test_game.py, test_session.py, test_link_e2e.py, test_humanize.py, test_stats.py)
```

## 4. Phase 1: protocol and crypto (`shared/chessfish_proto`)
Implement `PROTOCOL.md` exactly.
- `crypto.py`
  - `gen_psk() -> bytes` (32 random bytes) and `psk_to_text/psk_from_text` (urlsafe base64, no padding).
  - `make_hello(psk, host_id) -> (dict, eph_priv)`; `verify_hello(psk, msg) -> ok`; `make_welcome(...)`; `verify_welcome(...)`.
    HMAC-SHA256 over a canonical JSON encoding (sorted keys, no spaces) of all fields except `mac`, compared with `hmac.compare_digest`.
  - `derive_keys(shared_secret, psk, nonce_c, nonce_s) -> (k_pc2srv, k_srv2pc)`: HKDF-SHA256, length 64, split 32/32,
    `info = b"chessfish/v1" + nonce_c + nonce_s`, `salt = psk`.
- `envelope.py`: `Sealer(key, direction_byte)` / `Opener(key, direction_byte)`.
  The frame is `seq:u64be || ChaCha20Poly1305(key).encrypt(nonce, plaintext_json, aad=seq_bytes)`, where
  `nonce = direction(1B) + 3 zero bytes + seq(8B)`. The Opener rejects `seq <= last_seq`.
- `replay.py`: timestamp window ±30 s, plus an LRU set of seen `nonce_c` kept for 60 s.
- `messages.py`: pydantic models for every host-link type (`START`, `START_ACK`, `RESUME`, `STATE`, `FRIEND_MOVE`, `HOST_MOVE`,
  `CHAT`, `PING`, `PONG`, `NET`, `CONTROL`, `BYE`, `ERROR`) with a discriminated union on `type`.
- `friend_messages.py`: models for the **friend** protocol (section 5.3). They must contain **no** mode/engine fields.
- **Acceptance (pytest):** handshake round-trip; wrong PSK rejected; tampered byte rejected; replayed HELLO rejected;
  stale ts rejected; seq reuse rejected; keys differ per session.

## 5. Phase 2: server (`server/app`)
### 5.1 Game core
- `game.py` wraps `chess.Board`: `legal_dests()` → `{ "e2": ["e3","e4"], ... }` (chessground format), `apply_uci(uci)`
  (validates; promotion requires a piece), `san_list`, `outcome()` mapped to `{result, reason}` (checkmate, stalemate,
  insufficient, 75-move/fivefold auto; **claimable** 50-move/threefold are auto-claimed for simplicity), resign, draw agreement, flag (timeout vs
  insufficient-material draw rule).
- `clocks.py`: monotonic ms, Fischer increment, `pause()`/`resume()`, flag detection task at 100 ms resolution. The server owns time.
### 5.2 Session state machine (`session.py`)
`IDLE → COUNTDOWN(10s) → LIVE ⇄ PAUSED(host_lost | friend_lost) → ADJOURNED | ENDED → IDLE`
- One session at a time. Only the host link can start a session (`START`). The friend can't start or rematch anything.
- PAUSED: clocks frozen, grace timer runs (60 s). Resume on reconnect plus a valid `RESUME` token (host) or rejoin (friend).
- ADJOURNED: store FEN + move list + clocks + session_id + resume_token in SQLite (`store.py`, volume `/data`). Delete on resume or ENDED.
- Engine-mode moves: the host sends `HOST_MOVE` after its own humanized delay. The server treats `HOST_MOVE` the same way in both modes.
### 5.3 Friend protocol (`/play` WS on 6767, JSON)
- **Client → server:** `{"t":"hello","name":"..."}`, `{"t":"move","uci":"e7e8q"}`, `{"t":"resign"}`, `{"t":"draw_offer"}`,
  `{"t":"draw_accept"}`, `{"t":"chat","text":"..."}` (max 300 chars, rate-limited), `{"t":"ping","id":n}`.
- **Server → client:** `waiting`, `countdown{seconds}`, `state{fen, dests, last_move, check, turn, your_color, moves_san, clocks{white_ms,black_ms,running}}`,
  `move_rejected{reason}`, `gameover{result, reason}`, `banner{kind: "opponent_reconnecting"|"adjourned"|null, seconds_left}`,
  `chat{from: "Opponent"|"You", text}`, `draw_offered`, `pong{id}`.
- The name is stored server-side and shown to the host. The friend always sees the other side as **"Opponent"**.
- **Leak audit (mandatory test):** serialize every friend message in an engine game and a human game, and assert that the key sets
  and value shapes are identical and that no string contains "stockfish", "engine", "bot", "elo" or "skill" (case-insensitive).
### 5.4 Host link (`/link` WS on 6768)
Handshake per `PROTOCOL.md`. Drop the socket silently on any failed check (no error details to unauthenticated peers).
One active host link; a new authenticated link replaces the old one. Forward `FRIEND_MOVE`, `STATE`, `CHAT`, and `NET` (friend RTT).
### 5.5 Docker
`server/Dockerfile`: `python:3.12-slim`, non-root user, copies `app/`, `web/` and `shared/`, then `EXPOSE 6767 6768`, and the CMD runs both uvicorn apps
(one process, two servers via `asyncio.gather` on two `uvicorn.Server` configs). `HEALTHCHECK` hits `GET /healthz`.
Env: `CHESSFISH_PSK` (required, refuse to start without it), `CHESSFISH_GRACE_S=60`, `CHESSFISH_COUNTDOWN_S=10`.
- **Acceptance:** pytest with a fake host and fake friend (websockets clients) covering a full game, illegal move
  rejection, promotion, flag fall, resign, draw, host drop → pause → resume, grace expiry → adjourn → resume later, and the leak audit.

## 6. Phase 3: friend website (`server/web`)
- Plain HTML/JS, **no runtime CDN**. Vendor **chessground** (pin an exact version, keep its GPL-3.0 license file) and its CSS/piece set.
- Screens: **name entry** (remembered in localStorage) → **"Waiting for game"** → **10 s countdown** → board.
- Board: click and drag moves, legal-move dots from `dests`, last-move/check highlight, promotion picker, flip button, auto-orient to `your_color`.
- Side panel: Opponent / you with clocks (tenths under 10 s), SAN move list, Resign (with in-page confirm), Offer draw / Accept draw, chat.
- Ping badge (RTT ms, colored ok/warn/bad) and banners for opponent reconnecting (countdown) and adjourned.
- Sounds: move, capture, check, castle, start, end, low-time (10 s). Generate them with `tools/make_sounds.py` (synthesized, no
  license issues). Add a mute toggle, and start audio only after the first user interaction.
- Mobile friendly. Auto-reconnect the WS with backoff, then re-send `hello` with the name and get the current `state`.
- **Acceptance:** manual run with `uvicorn` plus the fake host script; play a full game in a browser and on a phone viewport.

## 7. Phase 4: link client (`host/chessfish_host/link/client.py`)
asyncio, in its own thread. API (used through Qt signals): `start_session(cfg)`, `send_move(uci)`, `send_chat(text)`, `control(kind)`.
Emits: `state_changed`, `friend_move`, `chat`, `link_stats(rtt, jitter, loss, state)`, `friend_net(rtt)`, `session_event`.
Reconnect backoff 0.5 → 1 → 2 → 4 → 8 → 10 s cap, then `RESUME`. Jitter = mean absolute delta of consecutive RTTs (last 30).
Loss = missing pongs / pings over the last 60 s. CLI harness `python -m chessfish_host.link.client --server host:6768` for testing before the GUI.

## 8. Phase 5: Chessfish GUI (PySide6)
**Threads:** UI · `EngineManager` (QThread) · link (asyncio thread) · stats (QThread) · dashboard (uvicorn thread). Data moves only through Qt signals and slots.
- **Two Stockfish processes:**
  - **Play engine** (engine mode only): `UCI_LimitStrength=true`, `UCI_Elo=<setting>` (or `Skill Level`), Threads 1–2, small Hash.
  - **Analysis engine:** full strength, `Threads = cores−1 (minus play threads)`, Hash from the wizard, `MultiPV = 5` (setting),
    `UCI_ShowWDL=true`, infinite analysis restarted on each position (`engine.analysis(board, multipv=N, info=chess.engine.INFO_ALL)`),
    UI updates throttled to 10 Hz.
- **Humanized delay** (`humanize.py`, pure function, unit tested):
  `base = remaining_ms / max(20, 40 − movenum/2) + 0.6*increment_ms`; multiply by a complexity factor
  `0.6 + 0.8 * clamp(spread_between_top2_cp / 150 inverse …)`. More close candidates means a longer think, and a forced move or only legal move gives
  ×0.25. Recaptures get ×0.5, the first 6 moves get ×0.4, and a log-normal noise factor (σ=0.35) is applied on top. Clamp to `[min_ms, max_ms]` and never use more than 8 %
  of remaining time. The engine's search runs inside that delay; the move is sent when the delay ends.
- **Main window layout:** left = board (custom `QGraphicsView`, SVG pieces from `chess.svg`, arrows for the top 3 MultiPV lines, eval bar);
  right = tabs **Engine · Probable moves · Game · Time · Network · Session log**; top bar = session controls and link light; bottom = chat.
- **Game setup dialog:** mode (engine/human), host colour (white/black/random), time control presets (1+0, 3+2, 5+3, 10+5, 15+10,
  30+0, untimed, custom), Elo slider (1320–3190) or Skill 0–20, humanize on/off, min/max delay.
- **Human mode:** the host moves on the GUI board (legal-move dots, promotion dialog). Analysis stays visible to the host.
- **Settings:** server address + port, re-pair key, Stockfish build override, Threads, Hash, MultiPV, dashboard on/off + LAN bind (off), and the folder for saved games.
- **Acceptance:** start a session against the local server container (or `uvicorn`), play engine and human games, and pull the network → pause → resume.

## 9. Phase 6: statistics (`host/chessfish_host/stats`)
Formulas (lichess-style, documented in code):
- Win% from cp: `50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)`; mate maps to 0/100.
- Move accuracy: `103.1668 * exp(-0.04354 * (wp_before - wp_after)) - 3.1669`, clamped 0–100. Game accuracy = mean of move accuracies (weighting optional).
- ACPL per player (cap a single loss at 1000 cp).
- Classification by win% drop: best (= top engine move), excellent ≤ 2, good ≤ 5, inaccuracy ≤ 10, mistake ≤ 20, blunder > 20. Thresholds go in config.
- **Probable moves:** softmax over MultiPV cp with temperature `T = 40 + (3200 − elo_estimate)/10` cp → probability per candidate.
  Show it for the side to move, plus the predicted friend reply (top of the softmax) and the eval swing per candidate.
- Threats: a null-move analysis (side to move passes) at low depth; skip it when in check.
- Material balance, mobility (legal move counts), hanging pieces (attacked and not defended, or attacked by a lower-value piece), king-zone attackers.
- Opening: bundle lichess `chess-openings` TSVs (CC0), match the longest EPD prefix, show ECO + name.
- Time: think time per move, clock curves (pyqtgraph). Network: RTT/jitter/loss for both legs, state timeline.
- **PGN writer:** on ENDED or ADJOURNED, save `games\YYYY-MM-DD_HHMM_<friendname>.pgn` with headers (Event "Chessfish", players
  "Host"/"Stockfish (Elo N)" vs friend name, TimeControl, Result), plus comments `{[%eval 0.34] [%clk 0:09:51]}` and move-grade NAGs (?!, ?, ??).
- **Acceptance:** unit tests on known positions for win%, accuracy, classification and the opening lookup; the PGN imports cleanly into lichess.

## 10. Phase 7: local web dashboard
FastAPI on `127.0.0.1:6769` (LAN bind only if the user enables it). `/` serves a static page, and `/ws` pushes the same stats JSON the GUI uses at 4 Hz.
It is read-only: no controls in v1.

## 11. Phase 8: installer (`installer/`)
### 11.1 Stockfish bundling
- `stockfish.lock.json` pins one official release (check github.com/official-stockfish/Stockfish/releases for the latest
  **stable** `sf_XX` tag and pin it) with these assets and SHA-256 hashes: `stockfish-windows-x86-64-avx512.zip`,
  `-vnni512`, `-bmi2`, `-avx2`, `-sse41-popcnt`, `stockfish-windows-x86-64.zip` (the asset names must match the release; verify them).
- `build.ps1` downloads them, verifies the hashes, extracts them to `build\stockfish\<variant>\stockfish.exe`, and copies `Copying.txt` (GPLv3) and a source link.
- `cpu_select.py` (at runtime): read flags with py-cpuinfo and pick the best supported variant (vnni512 → avx512 → bmi2 → avx2 → sse41-popcnt → x86-64).
  **Avoid bmi2 on AMD Zen 1/2** (slow pdep/pext): prefer avx2 there. Verify by running `bench 16 1 8` and fall back a level if it crashes.
### 11.2 PyInstaller
`chessfish.spec`: onedir, windowed, entry `chessfish_host.__main__`, include `data/`, the dashboard static files, and `theme.qss`. Exclude unused Qt modules.
### 11.3 Inno Setup (`chessfish.iss`)
```
[Setup]
AppName=Chessfish
DefaultDirName={src}\Chessfish
DisableDirPage=no
UsePreviousAppDir=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputBaseFilename=ChessfishSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
```
- Files: the PyInstaller dist plus all Stockfish variants (in `stockfish\<variant>\`) and licenses.
- Start menu shortcut, optional desktop icon, uninstaller, and "Run Chessfish" on finish.
- If the chosen dir isn't writable, the app falls back to `%APPDATA%\Chessfish` for config/logs/games (`paths.py`).
### 11.4 First-run wizard (`gui/wizard.py`)
1. **Stockfish:** detect the CPU, pick the variant, run bench, show NPS.
2. **Hardware:** cores/RAM from psutil → `Threads = cores−1`, `Hash = min(25% RAM, 4096)` MB (power of 2), editable.
3. **Server:** address + port (default 6768) → **Test connection** (full handshake + PING, show RTT or a clear error:
   unreachable / handshake rejected = wrong key).
4. **Pairing key:** Generate or Paste. Store it with `keyring` (Windows Credential Manager / DPAPI).
5. **Docker files:** write `docker\docker-compose.yml` and `docker\.env` (from templates, with `CHESSFISH_PSK` filled in) into the install
   dir, and open the folder. Show the instructions: copy to the VM, then run `docker compose up -d`.
   ```yaml
   services:
     chessfish-server:
       image: chessfish-server:latest   # or build: . if the build context is shipped
       restart: unless-stopped
       env_file: .env
       ports: ["6767:6767", "6768:6768"]
       volumes: ["./data:/data"]
   ```
   Ship the server image as `docker\chessfish-server.tar` (`docker save`), plus a `load-and-run.sh` that does `docker load` and then `compose up -d`.
   If Docker isn't on the build PC, ship the build context instead (`build: ./server`). **Ask the user which one they prefer.**
- **Acceptance:** run the installer on a clean Windows user/VM, both per-user and all-users, and with the default dir = the folder it was run from.
  The wizard completes, the server starts on the VM from the generated files, and a full game works end to end.

## 12. Phase 9: end-to-end test checklist
- [ ] Friend (via the existing Tailscale share, nothing changed) opens `http://<vm>:6767`, enters a name, and sees "Waiting for game".
- [ ] The host starts an engine game: the friend sees the 10 s countdown, then the board, and the moves come at human-like times.
- [ ] The host starts a human game: the friend's experience is indistinguishable (leak audit passes and timing looks plausible).
- [ ] Legal-move dots, promotion, castling, en passant, check, mate, stalemate, flag, resign and draw all work.
- [ ] Unplug the gaming PC's network: the friend sees "Opponent reconnecting… 0:59", clocks freeze, and replugging resumes exactly.
- [ ] Stay unplugged for over 60 s: adjourned on both sides; resume from the GUI later.
- [ ] Friend closes the tab mid-game: the host sees the disconnect timer, and the friend rejoins with the same name.
- [ ] A random client hitting 6768 without the key gets dropped and nothing happens.
- [ ] A PGN is saved on the gaming PC with evals/clocks/grades.
- [ ] The GUI stays responsive during analysis (no UI-thread blocking), and the dashboard shows the same stats.

## 13. Things to ask the user about (don't guess)
- Server image shipped as a `.tar` vs a build context (see 11.4).
- Any visual theme preference for the GUI/website.
- Anything that would touch the network config. **The answer for Tailscale is always no.**
