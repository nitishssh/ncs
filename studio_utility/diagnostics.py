"""Persistent logs and privacy-safe support reports."""
from __future__ import annotations

import logging
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from studio_utility.paths import CONFIG_DIR

LOG_DIR = CONFIG_DIR / "logs"
LOG_FILE = LOG_DIR / "studio-utility.log"


def configure_logging() -> Path:
    """Keep three small logs so intermittent camera failures survive a restart."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("studio_utility")
    if not any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == LOG_FILE for h in root.handlers):
        handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return LOG_FILE


def write_support_report(destination: str | Path) -> Path:
    """Write system facts and recent logs, excluding tokens, addresses and user settings."""
    configure_logging()
    destination = Path(destination)
    recent = LOG_FILE.read_text(encoding="utf-8", errors="replace")[-200_000:] if LOG_FILE.exists() else ""
    report = [
        "Studio Utility support report",
        f"Platform: {platform.platform()}",
        f"Python: {sys.version.split()[0]}",
        f"Architecture: {platform.machine()}",
        "",
        "Recent application log:",
        recent or "(empty)",
    ]
    destination.write_text("\n".join(report), encoding="utf-8")
    return destination
