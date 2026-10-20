# Decisions log (append-only)

Format: date — decision — why / source.

## 2026-10-20 — initial planning session
1. **Opponent mode: selectable per session** — host chooses in the Chessfish GUI; it is carried in the
   startup packet. Modes: `engine` (friend vs Stockfish) and `human` (friend vs host, Stockfish only analyses). *User choice.*
2. **Port 6767** (not 67). Port 67 is the DHCP server port (privileged, clashes with LAN DHCP). *User confirmed 6767.*
3. **Gaming PC stack: Python + PySide6**, plus an **optional local web dashboard** served by Chessfish
   *only on the gaming PC* (binds 127.0.0.1 by default). *User choice.*
4. **Friend's website shows:** board + legal-move dots, sounds, move list, clocks, promotion picker,
   resign / offer draw, **connection status/ping**, **chat**. **No** engine evals/best moves for the friend. *User choice.*
5. **Tailscale: runs on the Docker VM itself** (ACL part SUPERSEDED by 14) (not a sidecar). The VM also hosts a media server etc.,
   so a **Tailscale ACL must restrict shared users (`autogroup:shared`) to tcp:6767 only**. *User choice + safety requirement.*
6. **Drop-outs: pause clocks + grace period (default 60 s), then adjourn** (saved, resumable). *User choice.*
7. **Time control: host sets per session** (e.g. 10+5, 3+2, untimed) in the startup packet. *User choice.*
8. **Engine strength: host sets Elo/Skill in GUI** (UCI_LimitStrength/UCI_Elo or Skill Level + movetime). *User choice.*
9. **Rules authority = python-chess in the container**, not Stockfish (Stockfish does not validate moves).
   Prevents a tampered client or a disconnected PC from breaking game state. *Claude design, explained to user.*
10. **Connection direction: gaming PC → container** (outbound dial). Container never needs to reach the PC. *Claude design.*
11. **Host link on separate port 6768** (ACL part SUPERSEDED by 14); friend-facing site on 6767. Defense in depth
    on top of the encrypted handshake. *Claude design — confirm (see OPEN_QUESTIONS).*
12. **Crypto:** pre-shared 32-byte pairing key + ephemeral X25519 → HKDF → ChaCha20-Poly1305 per-frame AEAD
    (forward secrecy, replay protection). Runs inside Tailscale's WireGuard, so it's app-level auth more than secrecy. *Claude design.*

## 2026-10-20 — second round of answers
13. **Host link on port 6768 confirmed**; friend site on 6767. *User: "sure".*
14. **NEVER touch Tailscale settings/ACLs.** Decision 5's ACL idea is dropped. The link port is protected only by
    the PSK + X25519 handshake: anything without the pairing key is dropped before any game data. The friend can
    technically reach other services on the VM through the share; the user accepts that. *User, emphatically.*
15. **No Tailscale names needed in the plan.** The container listens on 6768 for the special packet. The address is
    entered once in the Chessfish setup wizard. *User.*
16. **Installer:** the gaming-PC side ships as `ChessfishSetup.exe` (PyInstaller + Inno Setup), handed to another PC.
    It bundles Stockfish builds for several CPU levels, auto-picks the right one, and sets Threads/Hash from the
    detected hardware. Nothing is assumed to be installed already. *User.*
17. **Friend enters a display name.** *User: yes.*
18. **Friend only ever sees "Opponent"**, with no way to tell engine from human: no engine label on the wire to
    the friend, identical message flow in both modes, **humanized engine think-time**, and host chat works in both modes. *User.*
19. **No rematch/requests from the friend.** Their page shows "Waiting for game", then a **countdown** when the host
    starts a session. *User.*
20. Host analysis is always visible to the host (default; Q6 was answered about the friend instead).

## 2026-10-20 — third round
21. **Game history: save PGNs on the gaming PC only** (with analysis annotations: evals, grades, clock times).
    The server keeps nothing except an adjourned game waiting to resume. *User.*
22. **Installer covers the gaming PC and the Docker files:** the wizard writes a ready `docker-compose.yml` + `.env` (with the
    pairing key) into an output folder for copying to the VM. *User.*
23. **Countdown before a game: 10 s.** *User.*
24. **Install location:** defaults to **the folder the setup .exe is run from** (Inno Setup `{src}\Chessfish`). The location can be
    changed on the directory page, and a **per-user / all-users** choice is offered (`PrivilegesRequiredOverridesAllowed=dialog`;
    per-user = no UAC). Portable layout: config, logs and PGNs live inside the install dir (`config\`, `logs\`, `games\`).
    If that dir isn't writable, they fall back to `%APPDATA%\Chessfish`. *User.*

## 2026-10-20 — build session (gaming PC)
25. **Dev Python = 3.13** (already installed) instead of installing 3.12; the container still uses `python:3.12-slim`. *Claude, to avoid installing software.*
26. **WELCOME echoes `nonce_c`** inside its MAC, binding it to the HELLO. Added an `EVENT {kind, detail}` link message (friend joined/left, draw offered, move rejected) and `resign` as a host CONTROL (human mode). *Claude design.*
27. **Docker steps deferred** — user: "docker steps come a bit later". Build server/host/website first; Dockerfile + compose come after.
28. **Sounds are synthesized WAVs** (`tools/make_sounds.py`, stdlib only) instead of mp3/ogg: no encoder needed, every browser plays WAV. *Claude.*
29. **chessground 9.2.1** vendored from npm with its GPL-3.0 LICENSE (`server/web/vendor/chessground/`), brown board + cburnett pieces (both inline data URIs, no CDN). *Claude.*
30. **One friend connection at a time:** a newer tab/device replaces the old one; the old page gets close code 4001 and shows "open in another tab" instead of reconnecting. *Claude.*
31. **Friend banner `paused`** added for a host-initiated pause (same in both modes). The friend's ping carries its last RTT so the host sees the friend leg (`NET`). *Claude.*
32. **Server skips its own pings to the host:** host PINGs → server PONGs; each side detects silence by read timeout (8 s). *Claude, simplification.*
33. **Stockfish 19 "universal" Windows build** is bundled instead of 6 per-CPU builds: the official release now ships one
    `stockfish-windows-x86-64-universal.exe` that picks avx512/bmi2/avx2/... itself. `cpu_select.py` is dropped; the wizard
    just runs `bench` to verify and shows NPS. Same goal as decision 16 (works on any CPU, auto-picked), less to ship. *Claude.*
34. **py-cpuinfo dropped** (not needed with the universal build); psutil gives cores/RAM. *Claude.*
35. **Wizard order:** Stockfish → Hardware → Pairing key → Server (+Test connection). The key page comes before the test,
    because the test needs the key. The Docker-files page is deferred with the other Docker steps (decision 27). *Claude.*
36. **Engine-mode draw offers** are answered automatically after 2–6 s: accept only from move 15 on and when the engine is not
    better (host-POV eval ≤ +0.30). Human mode shows the host a Yes/No prompt. *Claude.*
37. **Threat** = depth-10 search with the side to move "passing" (turn flipped, not a null move in history). *Claude.*
38. **Windowed exe fix:** `sys.stdout/stderr` are None without a console; `__main__` points them at devnull (uvicorn's
    logging crashed on `.isatty()`; the user spotted the error dialog). `build.ps1` runs `--selftest` in a real windowed process. *Claude.*
39. **Inno Setup 6 installed via winget** (user approved). `installer\build.ps1` now produces `build\installer\ChessfishSetup-0.1.0.exe`
    (~124 MB). Verified: silent per-user install → Stockfish in `{app}\stockfish\` → frozen selftest → Start-menu shortcut → uninstall
    (keeps config/games/logs). *User: "Install it".*
