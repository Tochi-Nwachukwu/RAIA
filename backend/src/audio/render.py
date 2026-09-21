"""Render: text -> audio through each language's TTS engine (config/tts.yaml).

Every paragraph of a script is synthesized and cached on its own, keyed by sha256(text + language +
voice). Fixed copy - station IDs, the disclosure, hotline readouts - therefore synthesizes once, ever,
even inside a segment that also carries the day's greeting. src/speech is called, never modified.

Synthesis never happens in a request handler: this runs in the pipeline, in worker processes (one
per device in tts.yaml), because YarnGPT takes several times real time on a laptop.
"""

from __future__ import annotations

import asyncio
import hashlib
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml
from pydantic import BaseModel

from src.models.segment import Segment
from src.settings import get_settings
from src.studio.presenters import load_presenters

SAMPLE_RATE = 24_000
PARAGRAPH_GAP_S = 0.35
PART = "\n\n"


class Route(BaseModel):
    engine: str
    language: str | None = None  # the engine's own name for the language
    note: str | None = None


class TTSConfig(BaseModel):
    routes: dict[str, Route]
    yarngpt_devices: list[str] = ["cpu"]


@cache
def tts_config() -> TTSConfig:
    return TTSConfig.model_validate(yaml.safe_load((get_settings().config_dir / "tts.yaml").read_text()))


def route(language: str) -> Route:
    return tts_config().routes.get(language, Route(engine="placeholder", note=f"No TTS route for {language}."))


def voice(segment: Segment) -> str | None:
    p = load_presenters().get(segment.presenter)
    return p.voice_for(segment.language) if p else None


def part_path(text: str, language: str, voice_name: str) -> Path:
    key = hashlib.sha256(f"{text}\x1f{language}\x1f{voice_name}".encode()).hexdigest()
    return get_settings().cache_dir / "tts" / key[:2] / f"{key}.wav"


@dataclass(frozen=True)
class Job:
    text: str
    language: str  # station language code
    voice: str
    out: str


# --- engines, run inside worker processes ----------------------------------------------------------

_engine = None


def _init_worker(devices: "mp.Queue") -> None:
    global _engine
    device = devices.get()
    import torch

    if device == "cpu":
        torch.set_num_threads(max(2, (os.cpu_count() or 4) // 2))
    from src.speech import YarnGPT

    _engine = YarnGPT(device=device)


def _synthesize(job: Job) -> tuple[str, float]:
    started = time.time()
    audio = _engine.synthesize(job.text, lang=route(job.language).language, speaker=job.voice)
    out = Path(job.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.wav")
    sf.write(tmp, audio, SAMPLE_RATE, subtype="PCM_16")
    tmp.replace(out)
    return job.out, time.time() - started


ENGINES = {"yarngpt"}  # engines with a synthesizer above; anything else renders nothing


# --- the stage's side ----------------------------------------------------------------------------

def jobs_for(segment: Segment) -> list[Job] | None:
    """The paragraphs this segment needs synthesized, or None when its language has no TTS engine."""
    r, v = route(segment.language), voice(segment)
    if r.engine not in ENGINES or not v:
        return None
    return [Job(p.strip(), segment.language, v, str(part_path(p.strip(), segment.language, v)))
            for p in segment.script.split(PART) if p.strip()]


async def synthesize_all(jobs: list[Job], log=print) -> None:
    """Synthesize every job whose audio is not cached yet, across the configured worker processes."""
    todo = sorted({j for j in jobs if not Path(j.out).exists()}, key=lambda j: -len(j.text))
    if not todo:
        return
    devices = tts_config().yarngpt_devices[: len(todo)]
    context = mp.get_context("spawn")
    queue = context.Queue()
    for device in devices:
        queue.put(device)
    words = sum(len(j.text.split()) for j in todo)
    log(f"render: synthesizing {len(todo)} paragraphs ({words} words) on {', '.join(devices)}")
    started, done = time.time(), 0
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=len(devices), mp_context=context, initializer=_init_worker, initargs=(queue,)) as pool:
        futures = [loop.run_in_executor(pool, _synthesize, job) for job in todo]
        for future in asyncio.as_completed(futures):
            await future
            done += 1
            if done % 5 == 0 or done == len(todo):
                log(f"render: {done}/{len(todo)} paragraphs, {(time.time() - started) / 60:.1f} min")


def compose(segment: Segment, jobs: list[Job], out: Path) -> Path:
    """The segment's audio: its paragraphs in order, with a short breath between them."""
    gap = np.zeros(int(PARAGRAPH_GAP_S * SAMPLE_RATE), dtype=np.float32)
    pieces = []
    for job in jobs:
        audio, rate = sf.read(job.out, dtype="float32")
        assert rate == SAMPLE_RATE
        pieces += [audio, gap]
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out, np.concatenate(pieces[:-1]) if pieces else gap, SAMPLE_RATE, subtype="PCM_16")
    return out
