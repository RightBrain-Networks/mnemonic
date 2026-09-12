#!/usr/bin/env python3
"""Run the standalone upload helper also shipped with installed Mnemonic skills."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "plugin/scripts/upload_artifact.py"),
        run_name="__main__",
    )
