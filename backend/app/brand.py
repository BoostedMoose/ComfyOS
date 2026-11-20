"""Product name and environment variables – the one place for the brand name."""
from __future__ import annotations

import os

NAME = "ComfyOS"
SLUG = "comfyos"
ENV_PREFIX = "COMFYOS_"


def env(name: str, default: str = "") -> str:
    """Read an environment variable with the prefix of the brand: env(`HOME`) reads COMFYOS_HOME."""
    return os.getenv(ENV_PREFIX + name, default)
