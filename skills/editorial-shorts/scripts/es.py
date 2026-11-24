#!/usr/bin/env python3
"""Entry point: python scripts/es.py --workspace <folder> <command> ...  (python 3.9+, standard library only)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from editorial_shorts.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
