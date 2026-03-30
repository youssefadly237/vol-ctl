"""Shared audio helpers — pactl/wpctl wrappers."""

from __future__ import annotations
import os
import subprocess
import sys

FOCUS_FILE = os.path.expanduser("~/.cache/vol-focus")
SOCKET_PATH = os.path.expanduser("~/.cache/vol-osd.sock")
STEP = "5%"


# ── low-level ────────────────────────────────────────────────────────────────


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


# ── sinks (output devices) ───────────────────────────────────────────────────


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
    data = _get_pw_dump()
    sinks: dict[str, str] = {}
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            node_id = str(obj.get("id"))
            name = props.get("node.description") or props.get("node.name", "")
            sinks[node_id] = name
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


def _get_pwpdump_to_wpctl_sink_map() -> dict[str, str]:
    """Map pw-dump sink IDs to wpctl sink IDs using node names."""
    import json

    try:
        data = json.loads(
            subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL)
        )
    except Exception:
        return {}

    wpctl_status = _run(["wpctl", "status"])
    wpctl_sinks = {}
    in_sinks = False
    for line in wpctl_status:
        if "├─ Sinks:" in line:
            in_sinks = True
            continue
        if in_sinks and "├─ Sources:" in line:
            break
        if in_sinks and "[vol:" in line:
            inner = line[2:].strip() if line.startswith(" │") else line.strip()
            vol_idx = inner.find("[vol:")
            if vol_idx > 0:
                name_part = inner[:vol_idx].strip()
                if name_part.startswith("*"):
                    name_part = name_part[1:].strip()
                wpctl_id = name_part.split(".")[0]
                wpctl_sinks[name_part.split(".", 1)[1].strip().lower()] = wpctl_id

    mapping = {}
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            pwdump_id = str(obj.get("id"))
            name = (props.get("node.description") or props.get("node.name", "")).lower()
            if name in wpctl_sinks:
                mapping[pwdump_id] = wpctl_sinks[name]
    return mapping


def get_sink_ids() -> list[str]:
    return list(get_sink_names().keys())


def _get_driver_to_sink_map() -> dict[str, str]:
    """Map driver-id to sink ID from wpctl status (real sinks only)."""
    # Get sink IDs from wpctl status by parsing it directly
    lines = _run(["wpctl", "status"])
    sink_ids = set()
    in_sinks = False
    for line in lines:
        if "├─ Sinks:" in line:
            in_sinks = True
            continue
        if in_sinks and "├─ Sources:" in line:
            break
        if in_sinks and "[vol:" in line:
            inner = line[2:].strip() if line.startswith(" │") else line.strip()
            vol_idx = inner.find("[vol:")
            if vol_idx > 0:
                name_part = inner[:vol_idx].strip()
                if name_part.startswith("*"):
                    name_part = name_part[1:].strip()
                sink_id = name_part.split(".")[0]
                sink_ids.add(sink_id)

    data = _get_pw_dump()
    mapping = {}
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        media_class = props.get("media.class", "")
        if "Sink" in media_class:
            node_id = str(obj.get("id"))
            driver_id = props.get("node.driver-id")
            # Only map to sinks that appear in wpctl status
            if driver_id and node_id in sink_ids:
                mapping[str(driver_id)] = node_id
    return mapping


# ── sink-inputs (per-app streams) ────────────────────────────────────────────


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


# ── focus state ──────────────────────────────────────────────────────────────


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


# ── cycle helper ─────────────────────────────────────────────────────────────


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


# ── wpctl / pactl actions ────────────────────────────────────────────────────


def volume_raise(fid: str) -> None:
    _call(["wpctl", "set-volume", fid, f"{STEP}+", "-l", "1.0"])


def volume_lower(fid: str) -> None:
    _call(["wpctl", "set-volume", fid, f"{STEP}-", "-l", "1.0"])


def volume_mute(fid: str) -> None:
    _call(["wpctl", "set-mute", fid, "toggle"])


def _get_sink_name(sink_id: str) -> str:
    """Get pactl sink name from wpctl sink ID (e.g., '160' -> 'bluez_output.78:15:2D:5C:40:89')."""
    lines = _run(["wpctl", "inspect", sink_id])
    for line in lines:
        if "node.name" in line:
            node_name = line.split("=")[1].strip().strip('"')
            parts = node_name.split(".")
            if len(parts) > 1 and parts[-1].isdigit():
                parts = parts[:-1]
            result = []
            for p in parts:
                if "_" in p and p.count("_") >= 2:
                    hex_parts = p.split("_")
                    if all(
                        len(h) == 2 and all(c in "0123456789ABCDEFabcdef" for c in h)
                        for h in hex_parts[:3]
                    ) and all(
                        len(h) == 2 and all(c in "0123456789ABCDEFabcdef" for c in h)
                        for h in hex_parts[3:]
                    ):
                        result.append(p.replace("_", ":"))
                    else:
                        result.append(p)
                else:
                    result.append(p)
            return ".".join(result)
    return ""


def _get_sink_input_id(stream_id: str) -> str:
    """Get pactl sink-input ID from pw stream ID using wpctl."""
    lines = _run(["wpctl", "inspect", stream_id])
    for line in lines:
        if "object.serial" in line:
            return line.split("=")[1].strip().strip('"')
    return ""


def _get_wpctl_to_pwpdump_sink_map() -> dict[str, str]:
    """Map wpctl sink IDs to pw-dump sink IDs using node names."""
    pwdump_to_wpctl = _get_pwpdump_to_wpctl_sink_map()
    return {v: k for k, v in pwdump_to_wpctl.items()}


def move_to_sink(input_id: str, sink_id: str) -> None:
    """Move stream to sink using pw-metadata with node name."""
    sink_node_names = _get_sink_node_names()
    sink_node_name = sink_node_names.get(sink_id)
    if sink_node_name:
        _call(["pw-metadata", input_id, "target.object", sink_node_name])
        _invalidate_cache()
        _invalidate_cache()


# ── sink (output device) volume ───────────────────────────────────────────────


def get_default_sink() -> str:
    """Return default sink ID (e.g., '62')."""
    lines = _run(["wpctl", "status"])
    for line in lines:
        if "* " in line and ". " in line:
            parts = line.strip().split()
            for i, p in enumerate(parts):
                if p == "*" and i + 1 < len(parts):
                    return parts[i + 1].rstrip(".")
    return ""


def sink_raise() -> None:
    sid = get_default_sink()
    if sid:
        _call(["wpctl", "set-volume", sid, f"{STEP}+", "-l", "1.0"])


def sink_lower() -> None:
    sid = get_default_sink()
    if sid:
        _call(["wpctl", "set-volume", sid, f"{STEP}-", "-l", "1.0"])


def sink_mute() -> None:
    sid = get_default_sink()
    if sid:
        _call(["wpctl", "set-mute", sid, "toggle"])


# ── socket IPC ───────────────────────────────────────────────────────────────


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
