# RAIA - Radio AI Africa

A civic radio station for African audiences, produced by AI agents and delivered as a scheduled
broadcast. Agents gather news from verified sources, corroborate claims, write spoken copy, translate
it into Pidgin, Hausa, Yoruba and Igbo, and render it to audio ahead of time. Every aired claim traces
back to named sources; the presenters are synthetic, and the station says so every hour.

- `backend/` - the pipeline, the API, and the listener desk. Start with `backend/README.md`.
- `frontend/` - the Next.js site: the public dial at `/`, the newsroom behind `/admin` (sign in with
  `test` / `password1`). See `frontend/README.md`.
  (`frontend-legacy/` is the static site it replaced, kept only as a fallback - safe to delete.)
- `RAIA_BUILD_PLAN.md` - the build plan.

Quick start (after `cd backend && uv sync` and filling in `backend/.env`):

```sh
cd backend && uv run python -m src.pipeline.morning --lang pcm,en,ha,yo,ig   # produce today's morning
cd backend && uv run uvicorn main:app --port 8000                            # serve it
cd frontend && npm install && npm run build && npm run start                 # open http://localhost:3000
```
