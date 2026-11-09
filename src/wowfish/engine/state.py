from __future__ import annotations

from enum import Enum


class State(Enum):
    IDLE = "idle"
    CHECKING = "checking"
    CASTING = "casting"
    WAIT_BITE = "wait_bite"
    INTERACT = "interact"
    PAUSED = "paused"
    STOPPED = "stopped"
