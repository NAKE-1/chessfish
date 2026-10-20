# Chessfish — project memory (read first)

All project memory lives in Markdown files in this directory. Keep them current.

| File | What it holds |
|---|---|
| `PLAN.md` | Architecture, components, repo layout, build phases |
| `IMPLEMENTATION_PLAN.md` | Step-by-step build + installer plan for the Claude on the gaming PC |
| `PROTOCOL.md` | Host-link wire protocol: encrypted startup packet, heartbeat, drop-out/resume |
| `DECISIONS.md` | Every decision made with the user + reasoning (append-only log) |
| `OPEN_QUESTIONS.md` | Things still unresolved — ask the user, don't guess |

## Working rules (from the user)
- **Ask the user whenever unsure** (use AskUserQuestion). Record the answer in `DECISIONS.md`.
- All memory/notes go in `.md` files here, not elsewhere.
- **NEVER touch, change, or plan changes to Tailscale settings/ACLs.** The tailnet and share are fixed.
  Security comes from the app (encrypted handshake on the link port).
- **The friend must never be able to tell whether the opponent is Stockfish or the host.** They only see "Opponent".
- The gaming-PC side ships as a **Windows installer** handed off to another PC. It must set itself up (Stockfish included).

## One-paragraph summary
**Chessfish** = a PySide6 app on the gaming PC that runs Stockfish and shows verbose stats, plus a
Docker container ("chessfish-server") on a Proxmox VM serving a chess website on **port 6767**.
Both are on the same tailnet; the VM node is Tailscale-shared to an outside friend, who plays in a
browser. The gaming PC dials the container and sends a dynamically generated, encrypted startup
packet to begin a session; heartbeats keep it alive, drop-outs pause the game with a grace period.
The server (python-chess) is the authority on chess rules; Stockfish only plays/analyses.
