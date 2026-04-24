"""Compatibility shim for the moved vol-ctl CLI."""

from __future__ import annotations

from vol_ctl.cli import main

if __name__ == "__main__":
    main()
