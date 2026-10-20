import json
import tomllib
from dataclasses import asdict, dataclass, field, fields

import keyring
import tomli_w

from .paths import CONFIG_DIR, GAMES_DIR, default_stockfish

CONFIG_FILE = CONFIG_DIR / "config.toml"
SESSION_FILE = CONFIG_DIR / "session.json"  # the START of the current/adjourned game, for RESUME
KEYRING_SERVICE, KEYRING_USER = "Chessfish", "pairing-key"


@dataclass
class Config:
    server_host: str = ""
    link_port: int = 6768
    stockfish_path: str = ""
    threads: int = 1
    hash_mb: int = 256
    multipv: int = 5
    play_threads: int = 1
    # game setup defaults
    mode: str = "engine"
    host_color: str = "random"
    tc_initial_s: int = 600
    tc_increment_s: int = 5
    untimed: bool = False
    elo: int = 1800
    use_skill: bool = False
    skill: int = 10
    humanize: bool = True
    min_ms: int = 800
    max_ms: int = 20000
    countdown_s: int = 10
    grace_s: int = 60
    heartbeat_ms: int = 2000
    friend_elo_estimate: int = 1500
    # dashboard
    dashboard: bool = False
    dashboard_lan: bool = False
    games_dir: str = field(default_factory=lambda: str(GAMES_DIR))
    wizard_done: bool = False

    @property
    def link_url(self) -> str:
        return f"ws://{self.server_host}:{self.link_port}/link"

    @classmethod
    def load(cls) -> "Config":
        c = cls()
        if CONFIG_FILE.exists():
            data = tomllib.loads(CONFIG_FILE.read_text("utf-8"))
            known = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in known:
                    setattr(c, k, v)
        if not c.stockfish_path:
            c.stockfish_path = str(default_stockfish())
        return c

    def save(self):
        CONFIG_FILE.write_text(tomli_w.dumps(asdict(self)), "utf-8")


def load_psk() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def save_psk(text: str):
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, text)


def save_session(start_json: dict | None):
    if start_json is None:
        SESSION_FILE.unlink(missing_ok=True)
    else:
        SESSION_FILE.write_text(json.dumps(start_json), "utf-8")


def load_session() -> dict | None:
    try:
        return json.loads(SESSION_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return None
