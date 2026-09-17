"""Render the five commands, as the CLI actually prints them, to an SVG.

Runs against a fresh `asof new` project in a temporary directory so the
recording is reproducible and never depends on this repository's own state.
The SVG is what the README shows; regenerate it whenever the CLI's output
changes:

    uv run python docs/record_session.py
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import date
from pathlib import Path

import typer
from rich.console import Console

import asof.cli as cli
from asof.scaffold import scaffold

OUT = Path(__file__).resolve().parent / "session.svg"
TODAY = date(2026, 9, 16)


def main() -> None:
    console = Console(record=True, width=104, force_terminal=True, color_system="truecolor")
    cli.console = console
    home = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "alpha-lab"
        scaffold("alpha-lab", root, today=TODAY)
        os.chdir(root)
        steps = [
            ("asof status", ["status"]),
            (f"asof check momentum --as-of {TODAY}", ["check", "momentum", "--as-of", str(TODAY)]),
            ("asof backtest momentum", ["backtest", "momentum"]),
            (f"asof promote momentum --as-of {TODAY}", ["promote", "momentum", "--as-of", str(TODAY)]),
            ("asof export momentum", ["export", "momentum"]),
        ]
        for shown, argv in steps:
            console.print(f"[bold bright_black]$[/] [bold]{shown}[/]")
            with contextlib.suppress(typer.Exit, SystemExit):
                cli.app(argv, standalone_mode=False)
        os.chdir(home)  # Windows cannot delete a directory a process is standing in
    OUT.write_text(console.export_svg(title="asof — from a fresh project to a deployable bundle"), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
