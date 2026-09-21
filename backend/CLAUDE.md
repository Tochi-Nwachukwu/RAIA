# RAIA backend - notes for Claude Code

RAIA (Radio AI Africa) is a civic radio station produced by AI agents. The product is trust: every
aired claim traces back to named sources. Read `../RAIA_BUILD_PLAN.md` before changing behaviour.

## Rules that are not negotiable
- Python 3.12, uv, FastAPI, Pydantic v2, async throughout. No agent framework.
- Every model call goes through `src/llm.py`; no other module imports a provider SDK. Provider and
  model per tier are in `config/llm.yaml` (Claude via the Vercel AI Gateway by default).
- `src/speech/` (YarnGPT) is called by `src/audio/render.py`, never modified.
- Never invent a source URL, hotline number, statistic or quote. Hotlines are only read on air after
  `python -m src.hotlines --verify` found them on an official page; the same goes for
  `python -m src.editorial --verify` (settled facts, voter education) and
  `python -m src.ingest.registry --verify` (sources).
- Durations come from ffprobe on rendered files (`src/audio/measure.py`), never estimates. No model
  touches timestamp arithmetic (`src/schedule/builder.py` is a pure function).
- Sensitive stories need a human approval (`python -m src.gate.safety approve`); there is no override flag.
- A day's programme belongs to the newsroom, not to `clock.yaml`: `blocks_for_day` reads MongoDB
  (`schedule_entries`) and falls back to the standing clock. Generation runs as subprocesses, one at a
  time, and each bulletin publishes as it finishes.
- The gate reviews every segment in every language after translation. The script stage also has the
  same reviewer read the English first, so a flagged script is revised once, with the reason, instead
  of being vetoed in every language. Translations into Yoruba, Igbo and Hausa get a second reading of
  every number (`src/studio/translator.py`).
- A language model never decides a contradiction: `src/desk/verifier.py` rules do (settled fact >
  official tier > later published_at for dates; unresolved for figures without an official source).

## Layout
- `config/` sources, clock, presenters, editorial, hotlines, llm, tts (TTS engine per language).
- `src/pipeline/stages.py` - the 16 stages; `morning.py`, `update.py`, `insert.py` are the entry points.
  Every stage checkpoints to `runs/<date>/` and can be re-run alone (`--only`, `--from`, `--to`).
- `src/api/` read-only handlers over `runs/` (+ WhatsApp webhook, + `/admin`). `main.py` mounts routers only.
- `src/admin/` the newsroom: token auth, and the job runner that hands a bulletin to the pipeline.
- `tests/` - `uv run pytest`. `test_verifier.py` holds Test Case #1 on real archived pages.

## Working here
- Model responses and TTS audio are cached (`.cache/`); re-running a stage is free unless its prompt changed.
- Rendering is slow on a laptop (YarnGPT is several times real time); `config/tts.yaml` sets worker devices.
- Pidgin and Swahili have no TTS model yet: they route to the placeholder engine and publish as text.
