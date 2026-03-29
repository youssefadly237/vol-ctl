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
    """Return {sink_id: description}."""
    sinks: dict[str, str] = {}
    cur_id = None
    for line in _run(["pactl", "list", "sinks"]):
        if line.startswith("Sink #"):
            cur_id = line.split("#")[1].strip()
        elif cur_id and line.strip().startswith("Description:"):
            sinks[cur_id] = line.split(":", 1)[1].strip()
    return sinks


def get_sink_ids() -> list[str]:
    return [
        line.split("#")[1].strip()
        for line in _run(["pactl", "list", "sinks"])
        if line.startswith("Sink #")
    ]


# ── sink-inputs (per-app streams) ────────────────────────────────────────────


def get_streams() -> list[dict]:
    """Return [{id, name, vol, muted, sink_id, sink_name}] using pw-dump."""
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

        vol = props.get("meta.volume", 1.0)
        muted = props.get("mute", False)

        sink_id = props.get("target.object", "")

        streams.append(
            {
                "id": node_id,
                "name": name,
                "vol": vol,
                "muted": muted,
                "sink_id": str(sink_id),
                "sink_name": sink_names.get(str(sink_id), ""),
            }
        )

    return streams


def get_stream_ids() -> list[str]:
    return [str(s["id"]) for s in get_streams()]


def get_input_sink(input_id: str) -> str:
    """Return sink ID currently used by a sink-input."""
    lines = _run(["pactl", "list", "sink-inputs"])
    found = False
    for line in lines:
        if line.startswith(f"Sink Input #{input_id}"):
            found = True
        elif found and line.strip().startswith("Sink:"):
            return line.split(":", 1)[1].strip()
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
    _call(["wpctl", "set-volume", fid, f"{STEP}+"])


def volume_lower(fid: str) -> None:
    _call(["wpctl", "set-volume", fid, f"{STEP}-"])


def volume_mute(fid: str) -> None:
    _call(["wpctl", "set-mute", fid, "toggle"])


def move_to_sink(input_id: str, sink_id: str) -> None:
    _call(["pactl", "move-sink-input", input_id, sink_id])


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
        _call(["wpctl", "set-volume", sid, f"{STEP}+"])


def sink_lower() -> None:
    sid = get_default_sink()
    if sid:
        _call(["wpctl", "set-volume", sid, f"{STEP}-"])


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
