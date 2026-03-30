"""vol-ctl — per-app volume controller (entry point)."""

from __future__ import annotations
import os
import subprocess
import sys

from vol_osd.audio import (
    cycle,
    default_next,
    default_prev,
    get_sink_ids,
    get_input_sink,
    get_stream_ids,
    move_to_sink,
    send,
    set_focus,
    sink_lower,
    sink_mute,
    sink_raise,
    validate_focus,
    volume_lower,
    volume_mute,
    volume_raise,
)

SOCKET_PATH = os.path.expanduser("~/.cache/vol-osd.sock")


def _show() -> None:
    send("show")


def cmd_raise() -> None:
    fid = validate_focus()
    if fid:
        volume_raise(fid)
    _show()


def cmd_lower() -> None:
    fid = validate_focus()
    if fid:
        volume_lower(fid)
    _show()


def cmd_mute() -> None:
    fid = validate_focus()
    if fid:
        volume_mute(fid)
    _show()


def cmd_cycle(direction: str) -> None:
    ids = get_stream_ids()
    if not ids:
        _show()
        return
    from vol_osd.audio import get_focus

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
    sink_raise()
    _show()


def cmd_sink_lower() -> None:
    sink_lower()
    _show()


def cmd_sink_mute() -> None:
    sink_mute()
    _show()


def cmd_start() -> None:
    import os
    import time
    from ctypes.util import find_library

    if os.path.exists(SOCKET_PATH):
        print("vol-osd already running")
        return
    env = os.environ.copy()
    lib_path = find_library("gtk4-layer-shell")
    if lib_path:
        env["LD_PRELOAD"] = lib_path
    proc = subprocess.Popen(
        ["vol-osd"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    time.sleep(0.5)
    if proc.poll() is not None:
        _, stderr = proc.communicate()
        if stderr:
            print(stderr.decode(), file=sys.stderr)
        else:
            print(
                f"vol-osd failed to start (exit code {proc.returncode})",
                file=sys.stderr,
            )
        sys.exit(1)
    print("vol-osd started")


def cmd_kill() -> None:
    subprocess.call(
        ["pkill", "-f", "vol-osd"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass


USAGE = """\
Usage: vol-ctl <command>

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
  show         show OSD without changing anything
  start        start the vol-osd daemon
  kill         stop the vol-osd daemon
"""


def main() -> None:
    if len(sys.argv) < 2:
        _show()
        return

    cmd = sys.argv[1]
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
            _show()
        case "default-prev":
            default_prev()
            _show()
        case "show":
            _show()
        case "start":
            cmd_start()
        case "kill":
            cmd_kill()
        case _:
            print(USAGE, file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
