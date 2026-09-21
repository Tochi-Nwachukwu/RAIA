# RAIA backend

RAIA - Radio AI Africa - is a civic radio station produced by AI agents: they gather news and sport
from verified sources, corroborate claims, write spoken copy, translate it, and render it to audio
ahead of time. The product is trust: every aired claim traces back to named sources, and the station
says plainly what it could not confirm or refused to air. Build plan: `../RAIA_BUILD_PLAN.md`.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), FFmpeg (`brew install ffmpeg`), MongoDB on
`localhost:27017` (optional - the filesystem is authoritative for runs), and in `.env`:

- `AI_GATEWAY_API_KEY` - model calls (Claude via the Vercel AI Gateway; see `config/llm.yaml` to
  switch provider or models)
- `OPENAI_API_KEY` - embeddings for clustering

```sh
uv sync
uv run pytest                      # rule tests, Test Case #1 on real pages, gate, schedule, WhatsApp desk
```

The first render downloads YarnGPT and its audio codec (about 2.5 GB, into the Hugging Face cache).

## Run the station

```sh
# 1. Produce the morning (every block before noon), all languages. Checkpoints go to runs/<date>/.
uv run python -m src.pipeline.morning --date 2026-09-19 --lang pcm,en,ha,yo,ig

# 2. Serve it.
uv run uvicorn main:app --port 8000
```

Then open the player (Next.js): `cd ../frontend && npm install && npm run build && npm run start`,
which serves http://localhost:3000. See `../frontend/README.md`.

Stages: ingest, extract, cluster, verify, edit, enrich, script, continuity, translate, diacritics,
gate, render, measure, assemble, schedule, publish. Re-run any of them without the ones before:
`--from render`, `--only gate`, `--from script --to gate`, `--force`. Model calls and synthesized audio are cached, so a re-run
only pays for what changed. Rendering is the slow part: YarnGPT runs at several times real time on a
laptop (`config/tts.yaml` sets the worker devices).

Other entry points:

```sh
uv run python -m src.pipeline.update --date 2026-09-19        # midday and evening top-ups
uv run python -m src.pipeline.insert --date 2026-09-19 --block morning_drive --lang en --listener
uv run python -m src.gate.safety pending --date 2026-09-19     # sensitive stories waiting for a human
uv run python -m src.gate.safety approve <story_id> --by "Your Name" --date 2026-09-19
uv run python -m src.desk.verifier                             # Test Case #1: stale election dates caught
scripts/nightly.sh                                              # verify sources/hotlines/facts, then the morning run
```

## Keeping it honest

These checks fetch live pages and record what they confirmed; run them before a demo:

```sh
uv run python -m src.ingest.registry --verify --write   # every feed resolves and is recent; failures dropped
uv run python -m src.hotlines --verify --write           # every hotline number is on its official page
uv run python -m src.editorial --verify --write          # settled facts and voter-education lines
```

## Languages and voices

`config/tts.yaml` routes each language to a TTS engine. YarnGPT (`src/speech/`, never modified by the
pipeline) speaks English, Hausa, Yoruba and Igbo. **Nigerian Pidgin and Swahili are routed to a
placeholder**: their bulletins are still written, translated and gated, and publish as text until a
model is routed in. To add one, implement an engine in `src/audio/render.py` (`ENGINES`), then point
the language at it in `config/tts.yaml`, and give the presenters a voice for it in `config/presenters.yaml`.

Voices were picked for intelligibility, not by name: the Igbo presenter uses the YarnGPT2 model card's
best-ranked Igbo voice, and the sport presenter uses `osagie`, which whisper-1 transcribed best of eight
male voices on sport copy (17% of words misheard, against 25% for `emma`). YarnGPT still mishears
unusual names; a native speaker should listen to the Hausa, Yoruba and Igbo bulletins (build plan S6).

## The newsroom (`/admin`)

The station's own side: a day of programming drawn as a calendar, and the agents that fill it.

- **Sign in** with `ADMIN_USERNAME` / `ADMIN_PASSWORD` from `.env` (the demo ships `test` / `password1`
  so a judge can try it - change them for anything else). A signed token, valid for
  `ADMIN_SESSION_HOURS`, goes in the `Authorization` header; the signing key lives in `.cache/admin_secret`.
- **The day's programme lives in MongoDB** (`schedule_entries`), not in `clock.yaml`. Opening a day
  copies the standing clock into it once; after that the newsroom owns it, and
  `src/schedule/clock.py:blocks_for_day` makes the pipeline follow what the calendar shows.
- **Generate** hands one bulletin to the pipeline as a subprocess, one at a time (rendering is
  memory-bound), and follows its output into `generation_jobs`. Each bulletin publishes as it lands,
  so an interrupted day keeps what is already on air. Audio goes where it always does:
  `runs/<date>/<block>/<language>/final/*.mp3`.
- **Overnight** the agents build the coming day by themselves, at `NIGHTLY_BUILD_AT` (default 00:30,
  station time; `NIGHTLY_BUILD_ENABLED=false` turns it off).

| Endpoint | What it does |
|---|---|
| `POST /admin/session` | sign in; returns the token |
| `GET /admin/days`, `PATCH /admin/days/{date}` | the diary, and a day's status (open / review / complete) |
| `GET, POST /admin/days/{date}/entries` | the day's programme; opening a day seeds it from the clock |
| `PATCH, DELETE /admin/entries/{id}` | move, retime, rename, change languages, remove |
| `GET, PUT /admin/entries/{id}/script` | read what will be said, and edit it (the audio goes stale) |
| `POST /admin/entries/{id}/generate`, `POST /admin/days/{date}/build` | run the agents |
| `GET /admin/jobs`, `POST /admin/jobs/{id}/cancel` | follow or stop a run |
| `GET /admin/messages` | listeners' WhatsApp threads |

## API

| Endpoint | What it returns |
|---|---|
| `GET /now?lang=en` | what is on air now, the offset into it, and the next three entries |
| `GET /bulletins`, `GET /bulletins/{id}` | bulletins and their manifests (segments, scripts, schedule) |
| `GET /sources`, `/sources/stories`, `/sources/contradictions`, `/sources/rejections` | provenance |
| `GET /sources/airtime` | seconds of airtime per party in each bulletin, and whether it is balanced |
| `GET /hotlines` | verified hotlines with the date each was checked |
| `GET /station` | the disclosure, presenters, and the models in use |
| `GET, POST /whatsapp/webhook` | WhatsApp Cloud API webhook (set `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`; without them replies go to `runs/outbox.jsonl`) |

## Layout

See `CLAUDE.md` for the rules the code keeps and where things live.
