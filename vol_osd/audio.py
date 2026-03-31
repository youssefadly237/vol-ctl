"""Shared audio helpers — pactl/wpctl wrappers."""

from __future__ import annotations
import os
import subprocess
import sys

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


# sinks (output devices)


_PWDUMP_CACHE: list[dict] | None = None


def _get_pw_dump() -> list[dict]:
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
    import json

    try:
        data = json.loads(
            subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL)
        )
    except Exception:
        return []

    sinks = []
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            node_id = obj.get("id")
            name = props.get("node.description") or props.get("node.name", "")

            pw_props = (obj.get("info", {}).get("params", {}).get("Props") or [{}])[0]
            ch_vols = pw_props.get("channelVolumes", [1.0])
            vol_cubic = sum(ch_vols) / len(ch_vols) if ch_vols else 1.0
            vol = vol_cubic ** (1 / 3)
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
    data = _get_pw_dump()
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
    import json

    try:
        data = json.loads(
            subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL)
        )
    except Exception:
        return []

    # Build sink lookup maps
    sink_names = {}
    sink_node_names = {}
    driver_to_pwdump = {}
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            pwdump_id = str(obj.get("id"))
            sink_names[pwdump_id] = props.get("node.description") or props.get(
                "node.name", ""
            )
            sink_node_names[props.get("node.name")] = pwdump_id
            driver_id = props.get("node.driver-id")
            if driver_id:
                driver_to_pwdump[str(driver_id)] = pwdump_id
            else:
                # Sinks without driver-id map to themselves
                driver_to_pwdump[pwdump_id] = pwdump_id

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
        vol_cubic = sum(ch_vols) / len(ch_vols) if ch_vols else 1.0
        vol = vol_cubic ** (1 / 3)
        muted = pw_props.get("mute", False)

        # Resolve sink_id
        sink_id = props.get("target.object")
        if not sink_id:
            driver_id = props.get("node.driver-id")
            if driver_id:
                sink_id = driver_to_pwdump.get(str(driver_id))

        if sink_id:
            sink_id = str(sink_id)
            if sink_id.isdigit():
                pass  # already a pwdump id
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


def get_input_sink(input_id: str) -> str:
    """Return sink pwdump_id currently used by a stream from pw-dump."""
    data = _get_pw_dump()

    # Build driver-id -> pwdump sink ID mapping
    driver_to_pwdump = {}
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            pwdump_id = str(obj.get("id"))
            driver_id = props.get("node.driver-id")
            if driver_id:
                driver_to_pwdump[str(driver_id)] = pwdump_id
            else:
                # Sinks without driver-id map to themselves
                driver_to_pwdump[pwdump_id] = pwdump_id

    sink_node_names = {}
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            sink_node_names[props.get("node.name")] = str(obj.get("id"))

    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if str(obj.get("id")) == input_id:
            if props.get("media.class") != "Stream/Output/Audio":
                return ""

            target = props.get("target.object")
            if target:
                target_str = str(target)
                if target_str.isdigit():
                    return target_str
                return sink_node_names.get(target_str, "")

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
    _call(["wpctl", "set-volume", fid, f"{STEP}+", "-l", "1.0"])


def volume_lower(fid: str) -> None:
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


def default_next() -> None:
    """Cycle default sink to next."""
    sinks = get_sink_ids()
    if not sinks:
        return
    current_name = _get_default_sink_name()
    current_pw_id = None
    sink_node_names = _get_sink_node_names()
    for pw_id, node_name in sink_node_names.items():
        if node_name == current_name:
            current_pw_id = pw_id
            break

    if not current_pw_id:
        current_pw_id = sinks[0]

    next_idx = (sinks.index(current_pw_id) + 1) % len(sinks)
    set_default_sink(sinks[next_idx])


def default_prev() -> None:
    """Cycle default sink to previous."""
    sinks = get_sink_ids()
    if not sinks:
        return
    current_name = _get_default_sink_name()
    current_pw_id = None
    sink_node_names = _get_sink_node_names()
    for pw_id, node_name in sink_node_names.items():
        if node_name == current_name:
            current_pw_id = pw_id
            break

    if not current_pw_id:
        current_pw_id = sinks[0]

    prev_idx = (sinks.index(current_pw_id) - 1) % len(sinks)
    set_default_sink(sinks[prev_idx])


# socket IPC


def send(msg: str) -> None:
    import socket as _socket

    try:
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
            s.connect(SOCKET_PATH)
            s.sendall(msg.encode())
    except FileNotFoundError:
        print("vol-osd not running. Run 'vol-ctl start' first.", file=sys.stderr)
    except Exception as e:
        print(f"vol-osd error: {e}", file=sys.stderr)
