"""The station's small, fixed library of beds and stings (build plan §7, rule 9: no copyrighted music).

Generated here from sine tones, so the audio is RAIA's own and released as CC0 - no licence to
check. Run once; the files live in assets/audio/:

    uv run python -m src.audio.assets
"""

from pathlib import Path

import numpy as np
import soundfile as sf

from src.settings import get_settings

RATE = 24_000
LICENSE = """# RAIA station audio

`sting.wav` and `bed.wav` are generated from sine tones by `src/audio/assets.py`. They contain no
third-party recordings or compositions. Released under CC0 1.0 (public domain dedication):
https://creativecommons.org/publicdomain/zero/1.0/
"""


def _peak(audio: np.ndarray, dbfs: float) -> np.ndarray:
    return audio / np.max(np.abs(audio)) * 10 ** (dbfs / 20)


def sting() -> np.ndarray:
    """A rising three-note chime, 1.4 seconds."""
    t = np.arange(int(1.4 * RATE)) / RATE
    out = np.zeros_like(t)
    for start, freq in [(0.0, 523.25), (0.12, 659.25), (0.24, 783.99)]:
        local = np.clip(t - start, 0, None)
        envelope = np.where(t >= start, np.minimum(local / 0.005, 1) * np.exp(-local / 0.35), 0)
        out += envelope * (np.sin(2 * np.pi * freq * local) + 0.3 * np.sin(2 * np.pi * 2 * freq * local))
    return _peak(out, -6)


def bed() -> np.ndarray:
    """A soft sustained chord, 30 seconds, seamless as a loop (every partial completes whole cycles)."""
    seconds = 30
    t = np.arange(seconds * RATE) / RATE
    chord = sum(a * np.sin(2 * np.pi * f * t) for f, a in [(220.0, 1.0), (277.2, 0.7), (329.6, 0.6), (440.0, 0.3)])
    swell = 0.75 + 0.25 * np.sin(2 * np.pi * 0.1 * t)  # three slow swells per loop
    return _peak(chord * swell, -12)


def ensure_assets() -> dict[str, Path]:
    folder = get_settings().assets_dir / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    paths = {"sting": folder / "sting.wav", "bed": folder / "bed.wav"}
    for name, make in [("sting", sting), ("bed", bed)]:
        if not paths[name].exists():
            sf.write(paths[name], make().astype(np.float32), RATE, subtype="PCM_16")
    (folder / "LICENSE.md").write_text(LICENSE)
    return paths


if __name__ == "__main__":
    for name, path in ensure_assets().items():
        info = sf.info(path)
        print(f"{name}: {path} ({info.duration:.1f}s)")
