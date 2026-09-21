"""Presenters (config/presenters.yaml): named voices with fixed slots and verbal habits."""

from __future__ import annotations

from functools import cache

import yaml
from pydantic import BaseModel

from src.settings import get_settings


class Presenter(BaseModel):
    key: str
    name: str
    voices: dict[str, str]  # language -> TTS voice
    languages: list[str]
    slots: list[str]
    habits: list[str]

    def voice_for(self, language: str) -> str | None:
        return self.voices.get(language)


class StationTexts(BaseModel):
    station_id: str
    disclosure: str


def _raw() -> dict:
    return yaml.safe_load((get_settings().config_dir / "presenters.yaml").read_text())


@cache
def load_presenters() -> dict[str, Presenter]:
    return {key: Presenter(key=key, **spec) for key, spec in _raw()["presenters"].items()}


@cache
def station_texts() -> StationTexts:
    raw = _raw()
    return StationTexts(station_id=raw["station_id"].strip(), disclosure=raw["disclosure"].strip())


def presenter(key: str) -> Presenter:
    return load_presenters()[key]
