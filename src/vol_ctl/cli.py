"""vol-ctl - per-app volume controller (entry point)."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from vol_ctl.models import SinkState, SinkSummary, StatusPayload, StreamState
from vol_ctl.stream import stream_status
from vol_osd.audio import (
    cycle,
    default_next,
    default_prev,
    get_dbus_players,
    get_default_sink,
    get_focus,
    get_input_sink,
    get_sink_ids,
    get_sinks,
    get_stream_ids,
    get_streams,
    move_to_sink,
    set_focus,
    sink_lower,
    sink_mute,
    sink_raise,
    validate_focus,
    volume_lower,
    volume_mute,
    volume_raise,
)
from vol_osd.audio import (
    send as _send,
)


def _try_import_osd() -> bool:
    """Try to import OSD functionality. Returns True on success."""
    try:
        from ctypes.util import find_library

        if not find_library("gtk4-layer-shell"):
            return False
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        gi.require_version("Gdk", "4.0")
        return True
    except (ImportError, ValueError, OSError):
        return False


_osd_state = {"enabled": False, "available": False}


def _set_osd_enabled(enabled: bool) -> None:
    _osd_state["enabled"] = enabled
    if enabled:
        _osd_state["available"] = _try_import_osd()
    else:
        _osd_state["available"] = False


def _show(mode: str | None = None) -> None:
    if _osd_state["enabled"] and _osd_state["available"]:
        _send(f"show {mode}" if mode else "show")


def _run_on_focused_stream(action: Callable[[str], None]) -> None:
    fid = validate_focus()
    if fid:
        action(fid)
    _show()


def _run_sink_action(action: Callable[[], None], mode: str = "sinks") -> None:
    action()
    _show(mode)


def _to_level(vol: float) -> int:
    return int(vol * 100)


def _get_icon(muted: bool, level: int) -> str:
    """Return Nerd Font volume icon based on mute state and volume level."""
    if muted:
        return "\ueee8"  # 
    if level <= 33:
        return "\uf026"  # 
    if level <= 66:
        return "\uf027"  # 
    return "\uf028"  # 


def cmd_raise() -> None:
    _run_on_focused_stream(volume_raise)


def cmd_lower() -> None:
    _run_on_focused_stream(volume_lower)


def cmd_mute() -> None:
    _run_on_focused_stream(volume_mute)


def cmd_cycle(direction: str) -> None:
    ids = get_stream_ids()
    if not ids:
        _show()
        return
    new = cycle(ids, get_focus(), direction)
    set_focus(new)
    _show()


def cmd_sink(direction: str) -> None:
    fid = validate_focus()
    if not fid:
        _show()
        return
    sinks = get_sink_ids()
    if not sinks:
        _show()
        return
    cur_sink = get_input_sink(fid)
    new_sink = cycle(sinks, cur_sink, direction)
    if new_sink:
        move_to_sink(fid, new_sink)
    _show()


def cmd_sink_raise() -> None:
    _run_sink_action(sink_raise)


def cmd_sink_lower() -> None:
    _run_sink_action(sink_lower)


def cmd_sink_mute() -> None:
    _run_sink_action(sink_mute)


def _build_status_payload() -> StatusPayload:
    streams = get_streams()
    sinks = get_sinks()
    focused = validate_focus()
    default_sink_id = get_default_sink()
    dbus_players = get_dbus_players()
    dbus_player_names = {name.casefold() for name in dbus_players}

    resolved_default_sink: dict | None = None
    resolved_default_sink_id = ""
    if sinks:
        resolved_default_sink = next(
            (s for s in sinks if str(s.get("id")) == str(default_sink_id)),
            sinks[0],
        )
        resolved_default_sink_id = str(resolved_default_sink.get("id", ""))

    sink_summary = SinkSummary()
    if resolved_default_sink:
        resolved_default_sink_vol = float(resolved_default_sink.get("vol", 0.0))
        resolved_level = _to_level(resolved_default_sink_vol)
        resolved_muted = bool(resolved_default_sink.get("muted", False))
        sink_summary = SinkSummary(
            level=resolved_level,
            muted=resolved_muted,
            icon=_get_icon(resolved_muted, resolved_level),
        )

    sink_states = [
        SinkState(
            id=str(s.get("id", "")),
            name=str(s.get("name", "")),
            vol=float(s.get("vol", 0.0)),
            level=_to_level(float(s.get("vol", 0.0))),
            muted=bool(s.get("muted", False)),
            default=str(s.get("id", "")) == resolved_default_sink_id,
            icon=_get_icon(
                bool(s.get("muted", False)),
                _to_level(float(s.get("vol", 0.0))),
            ),
        )
        for s in sinks
    ]

    stream_states: list[StreamState] = []
    focused_id = ""
    for s in streams:
        sid = str(s.get("id", ""))
        name = str(s.get("name", ""))
        vol = float(s.get("vol", 0.0))
        is_focused = sid == focused
        if is_focused:
            focused_id = sid
        stream_states.append(
            StreamState(
                id=sid,
                name=name,
                vol=vol,
                level=_to_level(vol),
                muted=bool(s.get("muted", False)),
                focused=is_focused,
                sink_id=str(s.get("sink_id", "")),
                sink_name=str(s.get("sink_name", "")),
                dbus_capable=name.casefold() in dbus_player_names,
                icon=_get_icon(bool(s.get("muted", False)), _to_level(vol)),
            )
        )

    return StatusPayload(
        sink=sink_summary,
        sinks=sink_states,
        streams=stream_states,
        focused_id=focused_id,
        default_sink_id=resolved_default_sink_id,
        dbus_players=dbus_players,
    )


def _status_json() -> str:
    return json.dumps(_build_status_payload().to_dict())


def cmd_status() -> None:
    """Print status as JSON to stdout."""
    print(_status_json())


def cmd_stream() -> None:
    """Stream status JSON on relevant PipeWire events."""
    use_osd = _osd_state["enabled"] and _osd_state["available"]
    sys.exit(stream_status(_status_json, emit_osd=use_osd))


USAGE = """\
Usage: vol-ctl [--osd] <command>

Options:
  --osd    show OSD after command (requires GTK4/OSD); for
           'stream', also triggers OSD on events

Commands:
  raise        raise focused app volume 5%
  lower        lower focused app volume 5%
  mute         toggle mute for focused app
  cycle-next   select next audio stream
  cycle-prev   select previous audio stream
  sink-next    move focused app to next output device
  sink-prev    move focused app to previous output device
  sink-raise   raise default sink volume 5%
  sink-lower   lower default sink volume 5%
  sink-mute    toggle default sink mute
  default-next cycle default sink to next
  default-prev cycle default sink to previous
  show         show OSD without changing anything
  status       print status as JSON
  stream       stream status JSON on PipeWire events
"""


def main() -> None:
    use_osd = False

    if sys.argv[1:2] == ["--osd"]:
        use_osd = True
        sys.argv.pop(1)

    _set_osd_enabled(use_osd)

    if use_osd and not _osd_state["available"]:
        print(
            "warning: --osd requested but OSD not available (GTK4 not installed)",
            file=sys.stderr,
        )

    if len(sys.argv) < 2:
        _show()
        return

    cmd = sys.argv[1]
    mode = (
        sys.argv[2]
        if len(sys.argv) > 2 and cmd == "show" and sys.argv[2] in ("apps", "sinks")
        else None
    )
    match cmd:
        case "raise":
            cmd_raise()
        case "lower":
            cmd_lower()
        case "mute":
            cmd_mute()
        case "cycle-next":
            cmd_cycle("next")
        case "cycle-prev":
            cmd_cycle("prev")
        case "sink-next":
            cmd_sink("next")
        case "sink-prev":
            cmd_sink("prev")
        case "sink-raise":
            cmd_sink_raise()
        case "sink-lower":
            cmd_sink_lower()
        case "sink-mute":
            cmd_sink_mute()
        case "default-next":
            default_next()
            _show("sinks")
        case "default-prev":
            default_prev()
            _show("sinks")
        case "show":
            _show(mode)
        case "status":
            cmd_status()
        case "stream":
            cmd_stream()
        case _:
            print(USAGE, file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
