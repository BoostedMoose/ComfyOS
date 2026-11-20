"""Isolated environment for a test.

ComfyOS reads its paths at import (`paths.py`, `settings_store.py`). A test must therefore set its own
directories before it imports `app` – otherwise it would read and write in the real profile of the machine.
`isolate()` does exactly that and nothing beyond.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def root() -> Path:
    """Root of ComfyOS: first argument of the test (that is how run_all.py calls it), otherwise this repo."""
    return Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else REPO


def isolate(name: str) -> tuple[Path, Path]:
    """Set own directories for settings and data, make `backend` importable, cut off the keyring, switch the
    Basic/Pro gates to shadow mode.

    Returns (root of ComfyOS, temp directory of the test). Further environment variables are set by the test
    itself afterwards – but before importing the modules that read them.
    """
    base = root()
    tmp = Path(tempfile.mkdtemp(prefix=f"comfyos-{name}-"))
    os.environ["APPDATA"] = str(tmp / "appdata")        # settings, login, secrets
    os.environ["COMFYOS_HOME"] = str(tmp / "home")      # data/ and logs/
    for path in (base / "backend", base / "tests"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from app import entitlements, settings_store

    settings_store._keyring = lambda: None              # never use the real keyring of the machine
    # The shipped code enforces the plan (since 0.26). A test world has no licence and would run as Basic –
    # tests of other areas would then fail at the Pro gates. So the suite starts in shadow mode; tests of the
    # gates arm them themselves (`entitlements.ENFORCE = True`). test_entitlements checks the shipped value.
    entitlements.ENFORCE = False
    return base, tmp
