"""PipeWire event stream helpers for vol-ctl."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable

from vol_osd.audio import _invalidate_cache
from vol_osd.audio import send as _osd_send

_RELEVANT_INTERFACES = {
    "PipeWire:Interface:Node",
    "PipeWire:Interface:Device",
    "PipeWire:Interface:Metadata",
    "PipeWire:Interface:Link",
}


def _extract_objects(value: object) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [obj for obj in value if isinstance(obj, dict)]
    return []


def _is_relevant_object(obj: dict) -> bool:
    """Check if a pw-dump object is relevant for volume control.

    Handles two schemas from pw-dump --monitor:
    - Top-level 'type' field (e.g. "PipeWire:Interface:Node")
    - Embedded 'info.props.media.class' (e.g. "Audio/Sink", "Stream/Output/Audio")
    """
    obj_type = str(obj.get("type", ""))
    if obj_type in _RELEVANT_INTERFACES:
        return True

    info = obj.get("info")
    if not isinstance(info, dict):
        return False
    props = info.get("props")
    if not isinstance(props, dict):
        return False
    media_class = str(props.get("media.class", ""))
    return media_class.startswith("Audio/") or media_class == "Stream/Output/Audio"


def _is_sink_object(obj: dict) -> bool:
    props = obj.get("info", {}).get("props", {})
    return props.get("media.class", "").startswith("Audio/Sink")


def _is_stream_object(obj: dict) -> bool:
    props = obj.get("info", {}).get("props", {})
    return props.get("media.class") == "Stream/Output/Audio"


def stream_status(
    status_json_provider: Callable[[], str], emit_osd: bool = False
) -> int:
    """Emit status JSON whenever relevant PipeWire monitor objects change."""
    last_payload = ""

    def emit_if_changed(mode: str, force: bool = False) -> None:
        nonlocal last_payload
        _invalidate_cache()
        payload = status_json_provider()
        if force or payload != last_payload:
            print(payload, flush=True)
            if emit_osd:
                _osd_send(f"show {mode}")
            last_payload = payload

    emit_if_changed("apps", force=True)

    try:
        proc = subprocess.Popen(
            [
                "pw-dump",
                "--monitor",
                "--raw",
                "--indent",
                "0",
                "--no-colors",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        print("error: pw-dump not found in PATH", file=sys.stderr)
        return 1

    if not proc.stdout:
        print("error: failed to read pw-dump output", file=sys.stderr)
        proc.terminate()
        return 1

    decoder = json.JSONDecoder()
    buffer = ""

    try:
        while True:
            chunk = proc.stdout.readline()
            if not chunk:
                break
            buffer += chunk

            while True:
                buffer = buffer.lstrip()
                if not buffer:
                    break
                try:
                    value, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    break

                buffer = buffer[end:]
                objects = _extract_objects(value)
                if objects and any(_is_relevant_object(obj) for obj in objects):
                    has_sink = any(_is_sink_object(obj) for obj in objects)
                    has_stream = any(_is_stream_object(obj) for obj in objects)
                    if has_sink and not has_stream:
                        emit_if_changed("sinks")
                    else:
                        emit_if_changed("apps")
    except KeyboardInterrupt:
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()

    return 0
