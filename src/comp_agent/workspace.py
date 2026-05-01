from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return slug or "comp-package"


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value


def write_json(path: str | Path, payload: Any) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(to_jsonable(payload), indent=2), encoding="utf-8")
    return output


def read_json_file(path: str | Path) -> Any:
    """Read JSON file and return parsed data."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def write_csv(path: str | Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    import csv

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    return output


class ProjectWorkspace:
    def __init__(self, output_root: str | Path, project_name: str) -> None:
        self.root = Path(output_root) / slugify(project_name)
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "outputs"
        self.data = self.outputs / "data"
        self.graphics = self.outputs / "graphics"
        self.json = self.outputs / "json"
        self.sources = self.outputs / "sources"
        self.working = self.outputs / "working"

    def create(self) -> "ProjectWorkspace":
        for folder in (self.inputs, self.data, self.graphics, self.json, self.sources, self.working):
            folder.mkdir(parents=True, exist_ok=True)
        return self
