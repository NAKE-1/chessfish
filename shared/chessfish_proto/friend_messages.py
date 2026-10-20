"""Friend protocol (/play WS on 6767). Must never carry mode/engine info."""
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# client -> server
class Hello(_M):
    t: Literal["hello"]
    name: str = Field(min_length=1, max_length=24)


class Move(_M):
    t: Literal["move"]
    uci: str = Field(min_length=4, max_length=5)


class Resign(_M):
    t: Literal["resign"]


class DrawOffer(_M):
    t: Literal["draw_offer"]


class DrawAccept(_M):
    t: Literal["draw_accept"]


class ChatIn(_M):
    t: Literal["chat"]
    text: str = Field(min_length=1, max_length=300)


class PingIn(_M):
    t: Literal["ping"]
    id: int


FriendIn = Annotated[Union[Hello, Move, Resign, DrawOffer, DrawAccept, ChatIn, PingIn], Field(discriminator="t")]
_in = TypeAdapter(FriendIn)


def parse_in(obj) -> FriendIn:
    return _in.validate_python(obj)


# server -> client
class Waiting(_M):
    t: Literal["waiting"] = "waiting"


class Countdown(_M):
    t: Literal["countdown"] = "countdown"
    seconds: int


class FClocks(_M):
    white_ms: Optional[int]
    black_ms: Optional[int]
    running: Optional[Literal["white", "black"]]


class FState(_M):
    t: Literal["state"] = "state"
    fen: str
    dests: dict[str, list[str]]
    last_move: Optional[list[str]]
    check: bool
    turn: Literal["white", "black"]
    your_color: Literal["white", "black"]
    moves_san: list[str]
    clocks: FClocks


class MoveRejected(_M):
    t: Literal["move_rejected"] = "move_rejected"
    reason: str


class GameOver(_M):
    t: Literal["gameover"] = "gameover"
    result: str
    reason: str


class Banner(_M):
    t: Literal["banner"] = "banner"
    kind: Optional[Literal["opponent_reconnecting", "adjourned"]]
    seconds_left: Optional[int] = None


class ChatOut(_M):
    t: Literal["chat"] = "chat"
    # `from` is a Python keyword, so the field is aliased.
    from_: Literal["Opponent", "You"] = Field(alias="from")
    text: str


class DrawOffered(_M):
    t: Literal["draw_offered"] = "draw_offered"


class PongOut(_M):
    t: Literal["pong"] = "pong"
    id: int


def out(msg: BaseModel) -> dict:
    return msg.model_dump(mode="json", by_alias=True)
