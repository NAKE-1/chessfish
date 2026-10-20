"""GUI end-to-end smoke test (not run by pytest: needs Stockfish + a Qt platform).

python tests/gui_e2e.py [engine|human]
Starts a real server, a scripted friend (random legal moves), and the Chessfish main window (offscreen),
plays a game, drops the link mid-game, checks resume, and checks a PGN was written.
"""
import asyncio
import json
import os
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import chess  # noqa: E402
import uvicorn  # noqa: E402
import websockets  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.main import build  # noqa: E402
from chessfish_proto.crypto import gen_psk, psk_to_text  # noqa: E402
from chessfish_host import config as cfgmod  # noqa: E402
from chessfish_host.gui import main_window as mw  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "engine"
PSK = gen_psk()
TMP = Path(tempfile.mkdtemp())
log = []


def server_thread(ready):
    async def main():
        session, friend_app, link_app = build(PSK, str(TMP / "srv"))
        servers = [uvicorn.Server(uvicorn.Config(a, host="127.0.0.1", port=p, log_level="warning"))
                   for a, p in ((friend_app, 16767), (link_app, 16768))]
        for s in servers:
            s.install_signal_handlers = lambda: None
        ready.session = session
        ready.set()
        await asyncio.gather(session.run(), *(s.serve() for s in servers))
    asyncio.run(main())


def friend_thread(stop):
    async def main():
        async with websockets.connect("ws://127.0.0.1:16767/play") as ws:
            await ws.send(json.dumps({"t": "hello", "name": "Bob"}))
            pid = 0
            while not stop.is_set():
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 2))
                except asyncio.TimeoutError:
                    pid += 1
                    await ws.send(json.dumps({"t": "ping", "id": pid, "rtt": 3.0}))
                    continue
                log.append(m["t"])
                if m["t"] == "state" and m["dests"]:
                    await asyncio.sleep(random.uniform(0.2, 0.6))
                    b = chess.Board(m["fen"])
                    mv = random.choice(list(b.legal_moves))
                    await ws.send(json.dumps({"t": "move", "uci": mv.uci()}))
                if m["t"] == "gameover":
                    print("friend saw gameover", m)
    asyncio.run(main())


ready = threading.Event()
threading.Thread(target=server_thread, args=(ready,), daemon=True).start()
ready.wait()
time.sleep(1)
stop = threading.Event()
threading.Thread(target=friend_thread, args=(stop,), daemon=True).start()

mw.load_psk = lambda: psk_to_text(PSK)
cfg = cfgmod.Config.load()
cfg.server_host, cfg.link_port, cfg.games_dir = "127.0.0.1", 16768, str(TMP / "games")
cfg.mode, cfg.host_color, cfg.untimed, cfg.tc_initial_s, cfg.tc_increment_s = MODE, "white", False, 120, 1
cfg.countdown_s, cfg.grace_s, cfg.min_ms, cfg.max_ms, cfg.threads, cfg.hash_mb = 1, 20, 200, 600, 4, 64
cfg.elo = 1400
cfg.dashboard = True
cfgmod.save_session(None)

os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
app = QApplication([])
app.setStyleSheet((Path(mw.__file__).parent / "theme.qss").read_text("utf-8"))
win = mw.MainWindow(cfg)
win.show()
steps = {"started": False, "dropped": False, "resumed": False, "t0": time.monotonic()}


def human_move():
    s = win.state
    if MODE == "human" and s and s.phase == "LIVE" and s.turn == s.host_color:
        mv = random.choice(list(win.board.legal_moves))
        win.boardw.set_movable(None)
        win.on_board_move(mv.uci())


def poll():
    s = win.state
    el = time.monotonic() - steps["t0"]
    if not steps["started"] and s and s.friend_connected:
        steps["started"] = True
        win.start_game()
        print("started", MODE)
    if s and s.phase == "LIVE" and len(s.moves) >= 3 and not steps["dropped"]:
        steps["dropped"] = True
        print("dropping link at ply", len(s.moves))
        asyncio.run_coroutine_threadsafe(ready.session.host.close(), win.client.loop)  # crude: closes from the link loop
    if steps["dropped"] and not steps["resumed"] and s and s.phase == "LIVE":
        steps["resumed"] = True
        print("resumed at ply", len(s.moves))
    if steps["resumed"] and s and s.phase == "LIVE" and len(s.moves) >= 20 and not steps.get("shot"):
        steps["shot"] = True
        win.tabs.setCurrentIndex(0)
        win.grab().save(str(TMP / "live.png"))
        print("live screenshot", TMP / "live.png")

        def shoot():
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(channel="msedge")
                pg = b.new_page(viewport={"width": 1300, "height": 900})
                pg.goto("http://127.0.0.1:6769/")
                pg.wait_for_timeout(1500)
                pg.screenshot(path=str(TMP / "dashboard.png"))
                b.close()
            print("dashboard screenshot", TMP / "dashboard.png")
        shoot()  # blocks the UI briefly; the dashboard serves from its own thread
    if steps["resumed"] and s and s.phase == "LIVE" and len(s.moves) >= 30:
        win.client.control("resign")
    human_move()
    if (s and s.phase == "ENDED") or el > 150:
        games = list((TMP / "games").glob("*.pgn"))
        print("phase", s.phase if s else None, "result", s.result if s else None, "pgns", [g.name for g in games])
        if games:
            print(games[0].read_text()[:1500])
        print("analysis depth", win.snap and win.snap["lines"] and win.snap["lines"][0]["depth"])
        print("summary", win.record and win.record.summary())
        print("rtt samples", [round(v, 1) for _, v in win.net_panel.pc_data][-15:])
        print("think ms", win.record and [p.think_ms for p in win.record.plies if p.mover == chess.WHITE])
        win.grab().save(str(TMP / "window.png"))
        print("screenshot", TMP / "window.png")
        for i in range(win.tabs.count()):
            win.tabs.setCurrentIndex(i)
            win._refresh_record()
            win.grab().save(str(TMP / f"tab{i}.png"))
        stop.set()
        app.quit()


QTimer(win, interval=200, timeout=poll).start()
app.exec()
win.close()
