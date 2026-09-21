"""Assemble: ffmpeg turns each rendered segment into the file listeners hear - a sting between
segments, a bed under the headlines, loudness normalised (EBU R128, -16 LUFS), encoded as
constant-bitrate MP3 so browsers seek to the exact offset."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.audio.assets import ensure_assets

LOUDNESS = "loudnorm=I=-16:TP=-1.5:LRA=11"
BED_UNDER = 0.10  # bed gain under speech
ENCODE = ["-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", "-write_xing", "1"]
STING_BEFORE = {"station_id", "headlines", "story", "explainer", "sport", "hotlines"}


async def _ffmpeg(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec("ffmpeg", "-y", "-v", "error", *args,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode()[:300]}")


async def assemble_segment(kind: str, speech: Path | None, out: Path, bed_seconds: float = 20.0) -> Path:
    assets = ensure_assets()
    out.parent.mkdir(parents=True, exist_ok=True)
    if kind == "bed" or speech is None:
        fade_out = max(bed_seconds - 2.5, 0)
        await _ffmpeg("-stream_loop", "-1", "-i", str(assets["bed"]), "-t", f"{bed_seconds:.2f}",
                      "-af", f"afade=t=in:d=2,afade=t=out:st={fade_out:.2f}:d=2.5,loudnorm=I=-22:TP=-3", *ENCODE, str(out))
        return out

    inputs, count = ["-i", str(speech)], 1
    chain = []
    if kind == "headlines":  # a short instrumental under the headlines
        inputs += ["-stream_loop", "-1", "-i", str(assets["bed"])]
        count += 1
        chain.append(f"[1]volume={BED_UNDER},afade=t=in:d=1.5[bedmix];[0][bedmix]amix=inputs=2:duration=first:normalize=0[voice]")
    else:
        chain.append("[0]anull[voice]")
    if kind in STING_BEFORE:
        inputs += ["-i", str(assets["sting"])]
        chain.append(f"[{count}]apad=pad_dur=0.25[sting];[sting][voice]concat=n=2:v=0:a=1[joined]")
        count += 1
        chain.append(f"[joined]{LOUDNESS}[out]")
    else:
        chain.append(f"[voice]{LOUDNESS}[out]")
    await _ffmpeg(*inputs, "-filter_complex", ";".join(chain), "-map", "[out]", *ENCODE, str(out))
    return out
