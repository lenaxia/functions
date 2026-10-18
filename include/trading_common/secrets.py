"""Secret/config reader — Fission convention.

Every trading Function CR mounts the ONE shared sops Secret `trading`
(deploy/trading/secret-trading.yaml.template); Fission mounts it at
/secrets/<namespace>/<secret-name>/. The mount directory is discovered
at runtime by scanning SECRET_BASE_DIR — same convention as violetscans'
_discover_secret_dir(). Every lookup falls back to the process
environment (per-key), then to the caller's default — the `_secret()`
helper convention in matriarch/imaizumi.
"""
from __future__ import annotations

import os
from pathlib import Path

# Base of the Fission secret mounts; tests point this at a tmp tree.
SECRET_BASE_DIR = Path("/secrets/fission")

# The shared secret this deploy provisions (ns fission). Legacy
# per-function mounts (trading-<fn>) still resolve if ever re-staged.
_SHARED_NAME = "trading"
_SECRET_PREFIX = "trading-"


def _discover_mount() -> Path | None:
    """The mounted secret dir for this function, if any.

    Each trading Function CR mounts exactly one secret, so the happy
    path is a single directory under SECRET_BASE_DIR. If several ever
    appear, prefer the exact shared-secret name, then a unique
    `trading-*` one; anything ambiguous means no mount.
    """
    if not SECRET_BASE_DIR.is_dir():
        return None
    dirs = sorted(p for p in SECRET_BASE_DIR.iterdir() if p.is_dir())
    if len(dirs) == 1:
        return dirs[0]
    if len(dirs) > 1:
        exact = [p for p in dirs if p.name == _SHARED_NAME]
        if len(exact) == 1:
            return exact[0]
        pref = [p for p in dirs if p.name.startswith(_SECRET_PREFIX)]
        if len(pref) == 1:
            return pref[0]
    return None


def mount_desc() -> str:
    """Human-readable mount location for error messages."""
    m = _discover_mount()
    return str(m) if m else "none under %s" % SECRET_BASE_DIR


def secret(name: str, default: str = "") -> str:
    mount = _discover_mount()
    if mount is not None:
        path = mount / name
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
