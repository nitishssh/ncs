"""Durable import queue used to recover interrupted camera transfers."""
from __future__ import annotations

import json
import time
from pathlib import Path


class ImportJournal:
    def __init__(self, root: Path):
        self.path = Path(root) / ".studio-utility-imports.json"

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if data.get("version") == 1 and isinstance(data.get("items"), dict) else {"version": 1, "items": {}}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"version": 1, "items": {}}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def update(self, remote: str, destination: Path, status: str, error: str = "") -> None:
        data = self.read()
        data["items"][remote] = {
            "destination": str(destination),
            "status": status,
            "error": error,
            "updated": time.time(),
        }
        # Completed history is useful for support, but keep the journal bounded.
        if len(data["items"]) > 1000:
            ordered = sorted(data["items"].items(), key=lambda item: item[1].get("updated", 0), reverse=True)
            data["items"] = dict(ordered[:1000])
        self._write(data)

    def pending(self) -> list[tuple[str, Path]]:
        recoverable = {"queued", "downloading", "failed", "cancelled"}
        return [(remote, Path(item["destination"])) for remote, item in self.read()["items"].items()
                if item.get("status") in recoverable and item.get("destination")]
