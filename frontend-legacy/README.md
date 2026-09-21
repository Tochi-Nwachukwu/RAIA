# RAIA player

A static site - no build step. It talks to the backend API (default `http://localhost:8000`).

```sh
# terminal 1: the API (from backend/)
uv run uvicorn main:app --port 8000

# terminal 2: this site (from frontend/)
python3 -m http.server 5173
```

Open http://localhost:5173 and press **Tune in**. The player asks the server what time it is, finds what
is on air, loads that file and seeks to the offset: everyone listening in a language hears the same
sentence at the same moment. Outside a block's first airing it replays the latest bulletin with the
station bed underneath.

- `?at=2026-09-19T06:05:30+01:00` - pretend it is that time (useful for demos and checking sync)
- `?api=https://your-api-host` - point at another backend (remembered in the browser)
- Pidgin has no voice model yet, so choosing it shows the bulletin's script instead of audio.

To listen back instead, open **Bulletins** (http://localhost:5173/bulletins.html): every bulletin of the
day, with *Play from the top*, a player for each segment, and the script to read along
(`?lang=ha` shows one language).

Pages: Listen, Bulletins, Sources (every claim and its sources, where sources disagreed, and airtime by
party), What we refused (the rejection log), Hotlines, About (the disclosure and the models used).
