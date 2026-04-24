"""Shared audio helpers - pw-dump/wpctl wrappers."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping

from vol_osd import FOCUS_FILE, SOCKET_PATH, STEP

_DEFAULT_AUDIO_SINK = "@DEFAULT_AUDIO_SINK@"

_SINK = "Audio/Sink"
_SINK_INTERNAL = "Audio/Sink/Internal"
_SINK_CLASSES = (_SINK, _SINK_INTERNAL)


# low-level


def _call(args: list[str]) -> None:
    subprocess.call(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# dbus MPRIS2 helpers


def _get_mpris_players() -> dict[str, str]:
    """Return {app_name: dbus_path} for all MPRIS2 players."""
    if _Cache.mpris is not None:
        return _Cache.mpris

    try:
        import dbus

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
        _Cache.mpris = result if result else {}
        return _Cache.mpris
    except ImportError:
        return {}
    except Exception:
        return {}


def _set_dbus_volume(app_name: str, delta: float, current_pw: float = -1.0) -> bool:
    """Set volume for app via MPRIS2.

    Delta is relative (+0.05 or -0.05). If current_pw >= 0, use it as the base
    (to handle out-of-sync MPRIS).
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
        new_vol = max(0.0, min(1.0, round(current + delta, 2)))
        props.Set("org.mpris.MediaPlayer2.Player", "Volume", dbus.Double(new_vol))
        return True
    except Exception:
        return False


def get_dbus_players() -> list[str]:
    """Return MPRIS player identities currently available on DBus."""
    players = _get_mpris_players()
    return sorted((str(name) for name in players.keys()), key=str.casefold)


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


def _cubic_to_linear_vol(ch_vols: list[float]) -> float:
    """Convert cubic channel volumes to linear scalar (0.0-1.0).

    Takes the cube root of the per-channel average.
    """
    if not ch_vols:
        return 0.0
    vol_cubic = sum(ch_vols) / len(ch_vols)
    return round(vol_cubic ** (1 / 3), 2)


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
        if props.get("media.class") == _SINK:
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


class _Cache:
    pw_dump: list[dict] | None = None
    mpris: dict[str, str] | None = None


def _get_pw_dump() -> list[dict]:
    """Get pw-dump data, cached for efficiency. Always returns a list.

    Returns an empty list on error (pw-dump not available or parse failure).
    """
    if _Cache.pw_dump is None:
        import json

        try:
            output = subprocess.check_output(
                ["pw-dump"], stderr=subprocess.DEVNULL, timeout=5
            )
            _Cache.pw_dump = json.loads(output)
            if not isinstance(_Cache.pw_dump, list):
                _Cache.pw_dump = []
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
            json.JSONDecodeError,
        ):
            _Cache.pw_dump = []
    return _Cache.pw_dump


def _invalidate_cache() -> None:
    """Invalidate pw-dump and MPRIS cache."""
    _Cache.pw_dump = None
    _Cache.mpris = None


def get_sink_names() -> dict[str, str]:
    """Return {pwdump_id: description} from pw-dump."""
    return {s["id"]: s["name"] for s in get_sinks()}


def get_sinks() -> list[dict]:
    """Return [{id, name, vol, muted}] for all sinks from pw-dump."""
    data = _get_pw_dump()

    sink_map: dict[str, dict] = {}
    device_to_internal_id: dict[str, str] = {}

    for obj in data:
        props = obj.get("info", {}).get("props", {})
        media_class = props.get("media.class", "")
        if media_class not in _SINK_CLASSES:
            continue

        node_id = str(obj.get("id"))
        name = _get_sink_name(props)
        key = name
        device_id = props.get("device.id", "")

        is_internal = media_class == _SINK_INTERNAL
        pw_props = (obj.get("info", {}).get("params", {}).get("Props") or [{}])[0]
        ch_vols = pw_props.get("channelVolumes", [1.0])
        vol = _cubic_to_linear_vol(ch_vols)
        muted = pw_props.get("mute", False)

        if is_internal and device_id:
            device_to_internal_id[device_id] = node_id

        if key not in sink_map:
            sink_map[key] = {
                "id": node_id,
                "name": name,
                "vol": vol,
                "muted": muted,
            }
        elif is_internal:
            sink_map[key]["vol"] = vol
            sink_map[key]["muted"] = muted

    return list(sink_map.values())


def get_sink_ids() -> list[str]:
    return list(get_sink_names().keys())


# sink-inputs (per-app streams)


def get_streams() -> list[dict]:
    """Return [{id, name, vol, muted, sink_id, sink_name}] using pw-dump only."""
    data = _get_pw_dump()
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
        vol = _cubic_to_linear_vol(ch_vols)
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
    data = _get_pw_dump()
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


# wpctl actions


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
    _, sink_node_names, _ = _get_sink_data(_get_pw_dump())
    node_name_by_pwdump_id = {v: k for k, v in sink_node_names.items()}
    sink_node_name = node_name_by_pwdump_id.get(sink_id)
    if sink_node_name:
        _call(["pw-metadata", input_id, "target.object", sink_node_name])
        _invalidate_cache()


# sink (output device) volume


def _get_metadata_name(value: object) -> str:
    """Extract a node name from metadata value payload."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "name" and isinstance(item, str):
                return item
        return ""
    if isinstance(value, str):
        return value
    return ""


def get_default_sink() -> str:
    """Return default sink id using pw-dump metadata."""
    data = _get_pw_dump()
    default_node_name = ""

    for obj in data:
        if obj.get("type") != "PipeWire:Interface:Metadata":
            continue
        for meta in obj.get("metadata", []):
            key = meta.get("key")
            if key not in ("default.audio.sink", "default.configured.audio.sink"):
                continue
            default_node_name = _get_metadata_name(meta.get("value"))
            if default_node_name:
                break
        if default_node_name:
            break

    if not default_node_name:
        return ""

    for obj in data:
        props = obj.get("info", {}).get("props", {})
        media_class = props.get("media.class", "")
        if media_class not in _SINK_CLASSES:
            continue
        if props.get("node.name") == default_node_name:
            return str(obj.get("id"))

    return ""


def sink_raise() -> None:
    _call(["wpctl", "set-volume", _DEFAULT_AUDIO_SINK, f"{STEP}+", "-l", "1.0"])
    _invalidate_cache()


def sink_lower() -> None:
    _call(["wpctl", "set-volume", _DEFAULT_AUDIO_SINK, f"{STEP}-", "-l", "1.0"])
    _invalidate_cache()


def sink_mute() -> None:
    _call(["wpctl", "set-mute", _DEFAULT_AUDIO_SINK, "toggle"])
    _invalidate_cache()


def _find_default_id_for_sink(sink_id: str) -> str:
    """Find the ID that wpctl set-default accepts for a sink."""
    data = _get_pw_dump()
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if str(obj.get("id")) == sink_id:
            media_class = props.get("media.class", "")
            if media_class == _SINK_INTERNAL:
                device_id = props.get("device.id")
                if device_id:
                    for other in data:
                        other_props = other.get("info", {}).get("props", {})
                        if (
                            other_props.get("device.id") == device_id
                            and other_props.get("media.class") == _SINK
                        ):
                            return str(other.get("id"))
            return sink_id
    return sink_id


def set_default_sink(sink_id: str) -> None:
    """Set default sink by pw-dump ID using wpctl."""
    default_id = _find_default_id_for_sink(sink_id)
    _call(["wpctl", "set-default", default_id])
    _invalidate_cache()


def default_next() -> None:
    """Cycle default sink to next."""
    _default_sink_cycle(1)


def default_prev() -> None:
    """Cycle default sink to previous."""
    _default_sink_cycle(-1)


def _default_sink_cycle(delta: int) -> None:
    """Cycle default sink by delta (-1 or 1)."""
    sinks = get_sink_ids()
    if not sinks:
        return
    current_pw_id = get_default_sink()
    if not current_pw_id:
        current_pw_id = sinks[0]

    idx = sinks.index(current_pw_id)
    set_default_sink(sinks[(idx + delta) % len(sinks)])


# socket IPC


def send(msg: str, auto_start: bool = True) -> None:
    import socket as _socket

    from vol_osd.utils import clear_stale_socket, start_daemon_process, wait_for_socket

    try:
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
            s.connect(SOCKET_PATH)
            s.sendall(msg.encode())
    except (FileNotFoundError, ConnectionRefusedError):
        if not auto_start:
            return
        clear_stale_socket()
        print(
            "vol-osd not running or stale socket, attempting to start...",
            file=sys.stderr,
        )
        start_daemon_process()
        if wait_for_socket():
            try:
                with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
                    s.connect(SOCKET_PATH)
                    s.sendall(msg.encode())
            except Exception as e:
                print(f"vol-osd error after auto-start: {e}", file=sys.stderr)
        else:
            print("vol-osd started but socket never appeared", file=sys.stderr)
    except Exception as e:
        print(f"vol-osd error: {e}", file=sys.stderr)
