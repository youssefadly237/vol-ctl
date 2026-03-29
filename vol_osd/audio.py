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


def get_sink_names() -> dict[str, str]:
    """Return {sink_id: description} using wpctl."""
    lines = _run(["wpctl", "status"])
    sinks: dict[str, str] = {}
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
                sink_name = (
                    name_part.split(".", 1)[1].strip()
                    if "." in name_part
                    else name_part
                )
                sinks[sink_id] = sink_name
    return sinks


def get_sink_ids() -> list[str]:
    return list(get_sink_names().keys())


def _get_driver_to_sink_map() -> dict[str, str]:
    """Map driver-id to virtual sink ID (e.g., 65 -> 35 for Snapcast)."""
    mapping = {}
    sink_names = get_sink_names()
    for sink_id in sink_names:
        lines = _run(["wpctl", "inspect", sink_id])
        for line in lines:
            if "node.driver-id" in line:
                driver_id = line.split("=")[1].strip().strip('"')
                mapping[driver_id] = sink_id
    return mapping


# ── sink-inputs (per-app streams) ────────────────────────────────────────────


def get_streams() -> list[dict]:
    """Return [{id, name, vol, muted, sink_id, sink_name}] using pw-dump + wpctl."""
    import json

    try:
        output = subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL)
        data = json.loads(output)
    except Exception:
        return []

    streams = []
    sink_names = get_sink_names()

    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") != "Stream/Output/Audio":
            continue

        node_id = obj.get("id")
        name = props.get("application.name", props.get("media.name", "unknown"))

        vol = 1.0
        muted = False
        try:
            vol_output = subprocess.check_output(
                ["wpctl", "get-volume", str(node_id)], stderr=subprocess.DEVNULL
            ).decode()
            vol = float(vol_output.split(":")[1].strip())
        except Exception:
            pass

        try:
            mute_output = subprocess.check_output(
                ["wpctl", "get-mute", str(node_id)], stderr=subprocess.DEVNULL
            ).decode()
            muted = "yes" in mute_output.lower() or "1" in mute_output
        except Exception:
            pass

        sink_id = props.get("target.object", "") or get_input_sink(str(node_id))
        sink_name = sink_names.get(str(sink_id), "")

        streams.append(
            {
                "id": node_id,
                "name": name,
                "vol": vol,
                "muted": muted,
                "sink_id": str(sink_id),
                "sink_name": sink_name,
            }
        )

    return streams


def get_stream_ids() -> list[str]:
    return [str(s["id"]) for s in get_streams()]


def get_input_sink(input_id: str) -> str:
    """Return sink ID currently used by a stream (pw-node-id) using wpctl."""
    lines = _run(["wpctl", "inspect", input_id])
    driver_id = ""
    for line in lines:
        if "node.driver-id" in line:
            driver_id = line.split("=")[1].strip().strip('"')
    driver_map = _get_driver_to_sink_map()
    return driver_map.get(driver_id, driver_id)


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


def move_to_sink(input_id: str, sink_id: str) -> None:
    import sys

    print(f"DEBUG move_to_sink: input_id={input_id}, sink_id={sink_id}", flush=True)
    sink_name = _get_sink_name(sink_id)
    print(f"DEBUG move_to_sink: sink_name={sink_name}", flush=True)
    if not sink_name:
        return
    sink_input_id = _get_sink_input_id(input_id)
    print(f"DEBUG move_to_sink: sink_input_id={sink_input_id}", flush=True)
    if sink_input_id:
        _call(["pactl", "move-sink-input", sink_input_id, sink_name])


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
