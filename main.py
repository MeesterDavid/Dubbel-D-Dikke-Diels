#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

# Zorg dat src/ altijd op sys.path staat zodat `python main.py` overal direct werkt
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from tweakersearch.cli import app_main

if __name__ == "__main__":
    raise SystemExit(app_main())
