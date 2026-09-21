"""runs/<date>/: the filesystem stays authoritative for runs. Every stage writes its output here, and
every stage can resume from the previous stage's file."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from src.settings import get_settings

M = TypeVar("M", bound=BaseModel)


class RunDir:
    def __init__(self, day: date):
        self.day = day
        self.path = get_settings().runs_dir / day.isoformat()
        self.path.mkdir(parents=True, exist_ok=True)

    def file(self, name: str) -> Path:
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def exists(self, name: str) -> bool:
        return (self.path / name).exists()

    def write(self, name: str, value) -> Path:
        """Write a model, a list of models, or plain JSON data."""
        data = TypeAdapter(type(value)).dump_python(value, mode="json") if not isinstance(value, (dict, list)) else value
        if isinstance(value, list) and value and isinstance(value[0], BaseModel):
            data = [v.model_dump(mode="json") for v in value]
        path = self.file(name)
        tmp = path.with_suffix(path.suffix + ".tmp")
        if name.endswith(".jsonl"):
            tmp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in data))
        else:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(path)
        return path

    def read(self, name: str, model: type[M]) -> M:
        return model.model_validate_json((self.path / name).read_text())

    def read_list(self, name: str, model: type[M]) -> list[M]:
        text = (self.path / name).read_text()
        if name.endswith(".jsonl"):
            return [model.model_validate_json(line) for line in text.splitlines() if line.strip()]
        return [model.model_validate(row) for row in json.loads(text)]

    def read_json(self, name: str):
        return json.loads((self.path / name).read_text())
