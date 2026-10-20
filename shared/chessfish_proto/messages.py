"""Host-link messages (inside the encrypted envelope). See PROTOCOL.md sections 2-4."""
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeControl(_M):
    initial_s: int = Field(ge=1)
    increment_s: int = Field(ge=0, default=0)


class EngineCfg(_M):
    elo: Optional[int] = None
    skill: Optional[int] = None
    humanize: bool = True
    min_ms: int = 800
    max_ms: int = 20000


class Heartbeat(_M):
    interval_ms: int = 2000
    degraded_after_ms: int = 5000
    lost_after_ms: int = 8000


class FriendView(_M):
    show_ping: bool = True
    chat: bool = True
    opponent_name: str = "Opponent"


class Start(_M):
    type: Literal["START"] = "START"
    session_id: str
    resume_token: str
    mode: Literal["engine", "human"]
    host_color: Literal["white", "black", "random"]
    time_control: Optional[TimeControl] = None
    engine: Optional[EngineCfg] = None
    countdown_s: int = 10
    heartbeat: Heartbeat = Heartbeat()
    grace_s: int = 60
    start_fen: Optional[str] = None
    friend_view: FriendView = FriendView()


class StartAck(_M):
    type: Literal["START_ACK"] = "START_ACK"
    session_id: str
    fen: str
    host_color: Literal["white", "black"]


class Resume(_M):
    type: Literal["RESUME"] = "RESUME"
    session_id: str
    resume_token: str


class MoveRec(_M):
    uci: str
    san: str


class Clocks(_M):
    white_ms: Optional[int] = None
    black_ms: Optional[int] = None
    running: Optional[Literal["white", "black"]] = None


class State(_M):
    type: Literal["STATE"] = "STATE"
    session_id: Optional[str] = None
    phase: str
    fen: str
    moves: list[MoveRec] = []
    clocks: Clocks = Clocks()
    turn: Literal["white", "black"]
    host_color: Optional[Literal["white", "black"]] = None
    result: Optional[str] = None
    reason: Optional[str] = None
    friend_name: Optional[str] = None
    friend_connected: bool = False
    paused_reason: Optional[str] = None
    grace_left_s: Optional[int] = None
    countdown_left_s: Optional[int] = None


class FriendMove(_M):
    type: Literal["FRIEND_MOVE"] = "FRIEND_MOVE"
    uci: str
    san: str


class HostMove(_M):
    type: Literal["HOST_MOVE"] = "HOST_MOVE"
    uci: str


class Chat(_M):
    type: Literal["CHAT"] = "CHAT"
    text: str = Field(max_length=300)


class Ping(_M):
    type: Literal["PING"] = "PING"
    seq: int
    t_send: float


class Pong(_M):
    type: Literal["PONG"] = "PONG"
    seq: int
    t_send: float


class Net(_M):
    type: Literal["NET"] = "NET"
    friend_rtt_ms: Optional[float] = None
    friend_connected: bool = False


class Control(_M):
    type: Literal["CONTROL"] = "CONTROL"
    kind: Literal["pause", "resume", "abort", "adjourn", "resign", "offer_draw", "accept_draw", "decline_draw"]


class Event(_M):
    """Server -> PC notifications: friend_joined, friend_left, draw_offered, move_rejected, ..."""
    type: Literal["EVENT"] = "EVENT"
    kind: str
    detail: Optional[str] = None


class Bye(_M):
    type: Literal["BYE"] = "BYE"
    reason: str = ""


class Error(_M):
    type: Literal["ERROR"] = "ERROR"
    code: str
    message: str = ""


LinkMsg = Annotated[
    Union[Start, StartAck, Resume, State, FriendMove, HostMove, Chat, Ping, Pong, Net, Control, Event, Bye, Error],
    Field(discriminator="type"),
]
_adapter = TypeAdapter(LinkMsg)


def parse(obj: dict) -> LinkMsg:
    return _adapter.validate_python(obj)


def dump(msg: BaseModel) -> dict:
    return msg.model_dump(mode="json")
