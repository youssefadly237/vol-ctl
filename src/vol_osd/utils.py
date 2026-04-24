"""Shared utilities."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from ctypes.util import find_library

from vol_osd import SOCKET_PATH


def clear_stale_socket() -> None:
    """Remove stale socket file if it exists."""
    if os.path.exists(SOCKET_PATH):
        try:
            os.unlink(SOCKET_PATH)
        except Exception:
            pass


def start_daemon_process() -> subprocess.Popen:
    """Start vol-osd daemon and return the process handle."""
    clear_stale_socket()

    env = os.environ.copy()
    lib_path = find_library("gtk4-layer-shell")
    if lib_path:
        env["LD_PRELOAD"] = lib_path

    return subprocess.Popen(
        ["vol-osd"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=env,
    )


def wait_for_socket(timeout: float = 3.0) -> bool:
    """Wait for socket to appear. Returns True if socket exists."""
    for _ in range(int(timeout * 10)):
        if os.path.exists(SOCKET_PATH):
            return True
        time.sleep(0.1)
    return False


def ensure_daemon_running() -> bool:
    """Start daemon and wait for socket.

    Returns True if socket appeared. On failure, prints error and exits
    if daemon process exited.
    """
    proc = start_daemon_process()
    if wait_for_socket():
        return True

    proc.poll()
    if proc.returncode is not None:
        try:
            err = proc.stderr.read().decode() if proc.stderr else ""
        except Exception:
            err = ""
        print(
            err or f"vol-osd failed to start (exit {proc.returncode})",
            file=sys.stderr,
        )
    else:
        print("vol-osd started but socket never appeared", file=sys.stderr)
        proc.terminate()
    return False


def kill_daemon_processes() -> None:
    """Stop running vol-osd processes and remove stale socket if present."""
    subprocess.call(
        ["pkill", "-f", "vol-osd"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    clear_stale_socket()
