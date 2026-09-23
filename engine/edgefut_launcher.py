"""Entrada do sidecar PyInstaller (imports absolutos)."""

import multiprocessing
import sys

from edgefut.main import run

if __name__ == "__main__":
    multiprocessing.freeze_support()
    run(sys.argv[1:])
