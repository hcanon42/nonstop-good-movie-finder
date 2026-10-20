#!/usr/bin/env python3
"""
Fetch Nonstop Kino program listings, look up Letterboxd ratings, sort best → worst.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _use_project_venv() -> None:
    """Re-run under .venv so taste scoring can import scikit-learn."""
    root = Path(__file__).resolve().parent
    venv = root / ".venv"
    python = venv / "bin" / "python"
    if not python.is_file():
        python = venv / "Scripts" / "python.exe"
    if not python.is_file():
        return
    try:
        if Path(sys.prefix).resolve() == venv.resolve():
            return
    except OSError:
        return
    os.execv(str(python), [str(python), *sys.argv])


if __name__ == "__main__":
    _use_project_venv()
    from goodmoviefinder.cli import main

    raise SystemExit(main())
