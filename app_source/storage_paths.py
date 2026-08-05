"""Centralized paths for all user data stored outside the source workspace."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


SETTINGS_PATH = Path("config/storage.json")
USER_SETTINGS_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "StockAI" / "storage.json"


def application_directory() -> Path:
    """Return the source or executable directory.

    Returns:
        The folder containing ``StockAI.exe`` when frozen, otherwise the
        folder containing this module.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_storage_root() -> Path:
    """Return the portable first-run data location beside the application."""
    return application_directory() / "StockAI_Data"


def _default_settings() -> dict[str, Any]:
    """Load the bundled layout and replace machine-specific root settings."""
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    settings["root"] = str(default_storage_root())
    return settings


def _load_user_settings() -> dict[str, Any] | None:
    """Load a usable per-machine configuration without creating folders."""
    if not USER_SETTINGS_PATH.exists():
        return None
    try:
        settings = json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
        root = Path(str(settings["root"])).expanduser()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    anchor = root.anchor
    if anchor and not Path(anchor).exists():
        return None
    return settings


def has_user_storage_config() -> bool:
    """Return whether this computer has a usable user-selected data folder."""
    return _load_user_settings() is not None


def configure_storage(root: Path) -> dict[str, Any]:
    """Persist a user-selected data root and create its standard directories.

    Args:
        root: User-selected folder for databases and archived data.

    Returns:
        The persisted storage configuration.

    Raises:
        ValueError: If the supplied path is blank.
        OSError: If the folder or settings file cannot be created.
    """
    if not str(root).strip():
        raise ValueError("storage root cannot be blank")
    root = root.expanduser().resolve()
    template = _default_settings()
    settings = {**template, "root": str(root)}
    USER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USER_SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    _create_storage_paths(settings)
    return settings


def _create_storage_paths(settings: dict[str, Any]) -> dict[str, Path]:
    """Build and create the standard data layout for validated settings."""
    root = Path(settings["root"])
    result = {
        "root": root,
        "history_database": root / settings["databases"]["history"],
        "decision_database": root / settings["databases"]["decisions"],
        **{name: root / relative for name, relative in settings["directories"].items()},
    }
    root.mkdir(parents=True, exist_ok=True)
    for name in ("raw_archive", "backups", "imports"):
        result[name].mkdir(parents=True, exist_ok=True)
    return result


def storage_paths() -> dict[str, Path]:
    """Load this computer's settings and ensure the data layout exists."""
    settings = _load_user_settings() or _default_settings()
    return _create_storage_paths(settings)
