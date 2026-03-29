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
    """Return [{id, name, vol, muted, sink_id, sink_name}]."""
    lines = _run(["pactl", "list", "sink-inputs"])
    sink_names = get_sink_names()

    streams: list[dict] = []
    cur: dict = {}

    def flush() -> None:
        if cur.get("id") is not None:
            sid = cur.get("sink_id", "")
            cur["sink_name"] = sink_names.get(sid, f"sink-{sid}")
            if not cur["name"]:
                cur["name"] = f"stream-{cur['id']}"
            streams.append(dict(cur))

    for line in lines:
        if line.startswith("Sink Input #"):
            flush()
            cur = {
                "id": int(line.split("#")[1].strip()),
                "name": "",
                "vol": 1.0,
                "muted": False,
                "sink_id": "",
            }
            continue
        if not cur:
            continue
        s = line.strip()
        if s.startswith("Sink:"):
            cur["sink_id"] = s.split(":", 1)[1].strip()
        elif s.startswith("Mute:"):
            cur["muted"] = s.split(":", 1)[1].strip().lower() == "yes"
        elif s.startswith("Volume:"):
            for part in s.split("/"):
                part = part.strip()
                if part.endswith("%"):
                    try:
                        cur["vol"] = int(part[:-1]) / 100.0
                    except ValueError:
                        pass
                    break
        elif s.startswith("application.name") and not cur["name"]:
            cur["name"] = s.split("=", 1)[1].strip().strip('"')
        elif s.startswith("media.name") and not cur["name"]:
            cur["name"] = s.split("=", 1)[1].strip().strip('"')
        elif s.startswith("node.name") and not cur["name"]:
            cur["name"] = s.split("=", 1)[1].strip().strip('"')

    flush()
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
