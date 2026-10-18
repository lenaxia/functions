"""Secret/config reader — Fission convention.

Reads from the Fission secret mount /secrets/fission/trading/<KEY> first
(secret name `trading`), falls back to the process environment. Matches the
`_secret()` helper convention in matriarch/imaizumi.
"""
from __future__ import annotations

import os
from pathlib import Path

SECRET_NAME = "trading"
MOUNT = Path("/secrets/fission") / SECRET_NAME


def secret(name: str, default: str = "") -> str:
    path = MOUNT / name
    if path.exists():
        return path.read_text().strip()
    return os.getenv(name, default)


def secret_int(name: str, default: int) -> int:
    try:
        return int(float(secret(name, str(default))))
    except ValueError:
        return default


def flag(name: str, default: str = "1") -> bool:
    return secret(name, default).strip().lower() in ("1", "true", "yes", "on")
