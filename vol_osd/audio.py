"""Shared audio helpers — pactl/wpctl wrappers."""

from __future__ import annotations
import os
import subprocess
import sys
from functools import lru_cache

FOCUS_FILE = os.path.expanduser("~/.cache/vol-focus")
SOCKET_PATH = os.path.expanduser("~/.cache/vol-osd.sock")
STEP = "5%"


# low-level


def _run(args: list[str]) -> list[str]:
    try:
        return (
            subprocess.check_output(args, stderr=subprocess.DEVNULL, timeout=2)
            .decode(errors="replace")
            .splitlines()
        )
    except Exception:
        return []


def _call(args: list[str]) -> None:
    subprocess.call(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# dbus MPRIS2 helpers


@lru_cache(maxsize=1)
def _get_mpris_players() -> dict[str, str]:
    """Return {app_name: dbus_path} for all MPRIS2 players."""
    import dbus

    try:
        bus = dbus.SessionBus()
        names = [
            str(n)
            for n in (bus.list_names() or [])
            if n.startswith("org.mpris.MediaPlayer2.")
        ]
        result = {}
        for name in names:
            try:
                obj = bus.get_object(name, "/org/mpris/MediaPlayer2")
                props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
                name_prop = props.Get("org.mpris.MediaPlayer2", "Identity")
                result[str(name_prop)] = name
            except Exception:
                continue
        return result
    except Exception:
        return {}


def _set_dbus_volume(app_name: str, delta: float, current_pw: float = -1.0) -> bool:
    """Set volume for app via MPRIS2. delta is relative (+0.05 or -0.05).
    If current_pw >= 0, use it as the base (to handle out-of-sync MPRIS).
    """
    try:
        import dbus
    except ImportError:
        return False

    players = _get_mpris_players()
    mpris_name = None
    for name, path in players.items():
        if name.lower() == app_name.lower():
            mpris_name = path
            break
    if not mpris_name:
        return False

    try:
        bus = dbus.SessionBus()
        obj = bus.get_object(mpris_name, "/org/mpris/MediaPlayer2")
        props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
        if current_pw >= 0:
            current = current_pw
        else:
            current = float(props.Get("org.mpris.MediaPlayer2.Player", "Volume"))
        new_vol = max(0.0, min(1.0, current + delta))
        props.Set("org.mpris.MediaPlayer2.Player", "Volume", dbus.Double(new_vol))
        return True
    except Exception:
        return False


def _get_pipewire_volume(fid: str) -> float:
    """Get volume for stream from wpctl."""
    try:
        output = (
            subprocess.check_output(
                ["wpctl", "get-volume", fid], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
        parts = output.split()
        if len(parts) >= 2:
            return float(parts[1])
    except Exception:
        pass
    return -1.0


# sinks (output devices)


def _calculate_volume(ch_vols: list[float]) -> float:
    """Calculate volume from channel volumes (cubic root of average)."""
    if not ch_vols:
        return 1.0
    vol_cubic = sum(ch_vols) / len(ch_vols)
    return vol_cubic ** (1 / 3)


def _get_sink_name(props: dict) -> str:
    """Extract sink name from node properties."""
    return props.get("node.description") or props.get("node.name", "")


def _get_sink_data(
    data: list[dict],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Build sink lookup maps from pw-dump data.

    Returns (sink_names, sink_node_names, driver_to_pwdump).
    """
    sink_names = {}
    sink_node_names = {}
    driver_to_pwdump = {}
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            pwdump_id = str(obj.get("id"))
            sink_names[pwdump_id] = _get_sink_name(props)
            sink_node_names[props.get("node.name")] = pwdump_id
            driver_id = props.get("node.driver-id")
            if driver_id:
                driver_to_pwdump[str(driver_id)] = pwdump_id
            else:
                driver_to_pwdump[pwdump_id] = pwdump_id
    return sink_names, sink_node_names, driver_to_pwdump


def _get_stream_targets(data: list[dict]) -> dict[str, str]:
    """Extract stream target mappings from metadata."""
    stream_targets = {}
    for obj in data:
        if obj.get("type") == "PipeWire:Interface:Metadata":
            for m in obj.get("metadata", []):
                if m.get("key") == "target.object":
                    stream_targets[str(m.get("subject"))] = m.get("value")
    return stream_targets


_PWDUMP_CACHE: list[dict] | None = None


def _get_pw_dump() -> list[dict] | None:
    """Get pw-dump data, cached for efficiency."""
    global _PWDUMP_CACHE
    if _PWDUMP_CACHE is None:
        import json

        try:
            output = subprocess.check_output(
                ["pw-dump"], stderr=subprocess.DEVNULL, timeout=5
            )
            _PWDUMP_CACHE = json.loads(output)
        except Exception:
            _PWDUMP_CACHE = []
    return _PWDUMP_CACHE


def _invalidate_cache() -> None:
    """Invalidate pw-dump cache."""
    global _PWDUMP_CACHE
    _PWDUMP_CACHE = None


def get_sink_names() -> dict[str, str]:
    """Return {pwdump_id: description} from pw-dump."""
    return {s["id"]: s["name"] for s in get_sinks()}


def get_sinks() -> list[dict]:
    """Return [{id, name, vol, muted}] for all sinks from pw-dump."""
    data = _get_pw_dump() or []

    sinks = []
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            node_id = obj.get("id")
            name = _get_sink_name(props)

            pw_props = (obj.get("info", {}).get("params", {}).get("Props") or [{}])[0]
            ch_vols = pw_props.get("channelVolumes", [1.0])
            vol = _calculate_volume(ch_vols)
            muted = pw_props.get("mute", False)

            sinks.append(
                {
                    "id": str(node_id),
                    "name": name,
                    "vol": vol,
                    "muted": muted,
                }
            )

    return sinks


def _get_sink_node_names() -> dict[str, str]:
    """Return {pwdump_id: node_name} from pw-dump for pw-metadata."""
    data = _get_pw_dump() or []
    sink_node_names = {}
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            sink_node_names[str(obj.get("id"))] = props.get("node.name", "")
    return sink_node_names


def get_sink_ids() -> list[str]:
    return list(get_sink_names().keys())


# sink-inputs (per-app streams)


def get_streams() -> list[dict]:
    """Return [{id, name, vol, muted, sink_id, sink_name}] using pw-dump only."""
    data = _get_pw_dump() or []
    sink_names, sink_node_names, driver_to_pwdump = _get_sink_data(data)
    stream_targets = _get_stream_targets(data)

    streams = []
    for obj in data:
        info = obj.get("info", {})
        props = info.get("props", {})
        if props.get("media.class") != "Stream/Output/Audio":
            continue

        node_id = obj.get("id")
        name = props.get("application.name") or props.get("media.name", "unknown")

        pw_props = (info.get("params", {}).get("Props") or [{}])[0]
        ch_vols = pw_props.get("channelVolumes", [1.0])
        vol = _calculate_volume(ch_vols)
        muted = pw_props.get("mute", False)

        stream_id_str = str(node_id)
        sink_id = stream_targets.get(stream_id_str)

        if not sink_id:
            driver_id = props.get("node.driver-id")
            if driver_id:
                sink_id = driver_to_pwdump.get(str(driver_id))

        if sink_id:
            sink_id = str(sink_id)
            if sink_id.isdigit():
                pass
            else:
                sink_id = sink_node_names.get(sink_id, "")
        else:
            sink_id = ""

        streams.append(
            {
                "id": node_id,
                "name": name,
                "vol": vol,
                "muted": muted,
                "sink_id": sink_id,
                "sink_name": sink_names.get(sink_id, ""),
            }
        )

    return streams


def get_stream_ids() -> list[str]:
    return [str(s["id"]) for s in get_streams()]


def get_stream_name(stream_id: str) -> str | None:
    """Return stream app name for given stream ID."""
    for s in get_streams():
        if str(s["id"]) == stream_id:
            return s.get("name")
    return None


def get_input_sink(input_id: str) -> str:
    """Return sink pwdump_id currently used by a stream from pw-dump."""
    data = _get_pw_dump() or []
    _, sink_node_names, driver_to_pwdump = _get_sink_data(data)
    stream_targets = _get_stream_targets(data)

    stream_target = stream_targets.get(input_id)
    if stream_target:
        target_str = str(stream_target)
        if target_str.isdigit():
            return target_str
        return sink_node_names.get(target_str, "")

    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if str(obj.get("id")) == input_id:
            if props.get("media.class") != "Stream/Output/Audio":
                return ""
            driver_id = props.get("node.driver-id")
            if driver_id:
                return driver_to_pwdump.get(str(driver_id), str(driver_id))

    return ""


# focus state


def get_focus() -> str:
    try:
        return open(FOCUS_FILE).read().strip()
    except Exception:
        return ""


def set_focus(fid: str) -> None:
    os.makedirs(os.path.dirname(FOCUS_FILE), exist_ok=True)
    with open(FOCUS_FILE, "w") as f:
        f.write(fid)


def validate_focus() -> str:
    """Return current focus ID, auto-selecting first stream if stale."""
    focus = get_focus()
    ids = get_stream_ids()
    if focus not in ids:
        focus = ids[0] if ids else ""
        if focus:
            set_focus(focus)
    return focus


# cycle helper


def cycle(items: list[str], current: str, direction: str) -> str:
    """Return next/prev item in list, wrapping around."""
    if not items:
        return current
    try:
        idx = items.index(current)
    except ValueError:
        idx = -1
    if direction == "next":
        return items[(idx + 1) % len(items)]
    else:
        return items[(idx - 1) % len(items)]


# wpctl / pactl actions


def volume_raise(fid: str) -> None:
    name = get_stream_name(fid)
    if name:
        before = _get_pipewire_volume(fid)
        if before >= 0:
            if _set_dbus_volume(name, 0.05, before):
                after = _get_pipewire_volume(fid)
                if after > before:
                    return
    _call(["wpctl", "set-volume", fid, f"{STEP}+", "-l", "1.0"])


def volume_lower(fid: str) -> None:
    name = get_stream_name(fid)
    if name:
        before = _get_pipewire_volume(fid)
        if before >= 0:
            if _set_dbus_volume(name, -0.05, before):
                after = _get_pipewire_volume(fid)
                if after < before:
                    return
    _call(["wpctl", "set-volume", fid, f"{STEP}-", "-l", "1.0"])


def volume_mute(fid: str) -> None:
    _call(["wpctl", "set-mute", fid, "toggle"])


def move_to_sink(input_id: str, sink_id: str) -> None:
    """Move stream to sink using pw-metadata with node name."""
    sink_node_names = _get_sink_node_names()
    sink_node_name = sink_node_names.get(sink_id)
    if sink_node_name:
        _call(["pw-metadata", input_id, "target.object", sink_node_name])
        _invalidate_cache()


# sink (output device) volume


def get_default_sink() -> str:
    """Return default sink pwdump_id using pactl + pw-dump."""
    lines = _run(["pactl", "get-default-sink"])
    if not lines:
        return ""
    pactl_name = lines[0].strip()

    sink_node_names = _get_sink_node_names()
    for pwdump_id, node_name in sink_node_names.items():
        if node_name == pactl_name:
            return pwdump_id

    return ""


def sink_raise() -> None:
    sid = get_default_sink()
    if sid:
        _call(["wpctl", "set-volume", sid, f"{STEP}+", "-l", "1.0"])
        _invalidate_cache()


def sink_lower() -> None:
    sid = get_default_sink()
    if sid:
        _call(["wpctl", "set-volume", sid, f"{STEP}-", "-l", "1.0"])
        _invalidate_cache()


def sink_mute() -> None:
    sid = get_default_sink()
    if sid:
        _call(["wpctl", "set-mute", sid, "toggle"])
        _invalidate_cache()


def _get_default_sink_name() -> str:
    """Get pactl name of default sink."""
    lines = _run(["pactl", "get-default-sink"])
    return lines[0].strip() if lines else ""


def set_default_sink(sink_id: str) -> None:
    """Set default sink by pw-dump ID using pactl."""
    sink_node_names = _get_sink_node_names()
    pactl_name = sink_node_names.get(sink_id)
    if pactl_name:
        _call(["pactl", "set-default-sink", pactl_name])
        _invalidate_cache()


def _get_current_sink_id() -> str | None:
    """Get current default sink pwdump_id from pactl name."""
    current_name = _get_default_sink_name()
    if not current_name:
        return None
    sink_node_names = _get_sink_node_names()
    for pw_id, node_name in sink_node_names.items():
        if node_name == current_name:
            return pw_id
    return None


def default_next() -> None:
    """Cycle default sink to next."""
    sinks = get_sink_ids()
    if not sinks:
        return
    current_pw_id = _get_current_sink_id()
    if not current_pw_id:
        current_pw_id = sinks[0]

    next_idx = (sinks.index(current_pw_id) + 1) % len(sinks)
    set_default_sink(sinks[next_idx])


def default_prev() -> None:
    """Cycle default sink to previous."""
    sinks = get_sink_ids()
    if not sinks:
        return
    current_pw_id = _get_current_sink_id()
    if not current_pw_id:
        current_pw_id = sinks[0]

    prev_idx = (sinks.index(current_pw_id) - 1) % len(sinks)
    set_default_sink(sinks[prev_idx])


# socket IPC


def send(msg: str) -> None:
    import socket as _socket
    from vol_osd.ctl import cmd_start

    try:
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
            s.connect(SOCKET_PATH)
            s.sendall(msg.encode())
    except (FileNotFoundError, ConnectionRefusedError):
        # Socket missing or stale, try to start daemon
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except Exception:
                pass
        print(
            "vol-osd not running or stale socket, attempting to start...",
            file=sys.stderr,
        )
        cmd_start()
        try:
            with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
                s.connect(SOCKET_PATH)
                s.sendall(msg.encode())
        except Exception as e:
            print(f"vol-osd error after auto-start: {e}", file=sys.stderr)
    except Exception as e:
        print(f"vol-osd error: {e}", file=sys.stderr)
