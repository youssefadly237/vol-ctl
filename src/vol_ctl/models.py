"""Typed data models for vol-ctl status output."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class SinkSummary:
    level: int = 0
    muted: bool = False
    icon: str = ""


@dataclass(slots=True)
class SinkState:
    id: str
    name: str
    vol: float
    level: int
    muted: bool
    default: bool
    icon: str = ""


@dataclass(slots=True)
class StreamState:
    id: str
    name: str
    vol: float
    level: int
    muted: bool
    focused: bool
    sink_id: str
    sink_name: str
    dbus_capable: bool
    icon: str = ""


@dataclass(slots=True)
class StatusPayload:
    sink: SinkSummary = field(default_factory=SinkSummary)
    sinks: list[SinkState] = field(default_factory=list)
    streams: list[StreamState] = field(default_factory=list)
    focused_id: str = ""
    default_sink_id: str = ""
    dbus_players: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
