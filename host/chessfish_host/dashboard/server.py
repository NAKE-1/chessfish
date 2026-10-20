"""Read-only local stats dashboard. Binds 127.0.0.1 unless the user enables LAN."""
import asyncio
import threading

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from ..paths import RES_DIR

PORT = 6769


class Dashboard:
    def __init__(self, lan: bool = False):
        self.latest: dict = {}  # replaced wholesale by the UI thread; readers just take the reference
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.websocket("/ws")
        async def ws(sock: WebSocket):
            await sock.accept()
            try:
                while True:
                    await sock.send_json(self.latest)
                    await asyncio.sleep(0.25)
            except Exception:
                pass

        app.mount("/", StaticFiles(directory=RES_DIR / "dashboard" / "static", html=True))
        self.server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0" if lan else "127.0.0.1", port=PORT,
                                                    log_level="warning"))
        self.server.install_signal_handlers = lambda: None

    def start(self):
        threading.Thread(target=self.server.run, name="dashboard", daemon=True).start()

    def stop(self):
        self.server.should_exit = True
