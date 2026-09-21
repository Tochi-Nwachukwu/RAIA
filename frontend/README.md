# RAIA player (Next.js)

The station's site: the wall-clock player, every bulletin on demand, and the provenance pages. It talks
to the RAIA API, which defaults to `http://localhost:8000`.

```sh
npm install
npm run build && npm run start -- -p 3000    # http://localhost:3000
npm run dev                                   # development, with hot reload
```

The API location comes from `NEXT_PUBLIC_RAIA_API` (see `.env.local`), and `?api=https://your-host`
overrides it in the browser and is remembered.

## Pages

The public side is one page - a language, a dial and **Tune in**. Everything a newsroom needs sits
behind `/admin`, which is **not** gated by any login yet (hackathon demo).

| Page | What it is |
|---|---|
| `/` | **On air**: pick a language, press Tune in, watch the dial react to the sound. |
| `/admin/login` | Sign in (`test` / `password1` in the demo). |
| `/admin` | The diary: every day, its status, and how much audio exists. |
| `/admin/schedule/[date]` | The day as a calendar, 6am to 10pm: drag a bulletin to move it, drag its edge to retime it, click an empty stretch to add one, then Generate and listen. |
| `/admin/messages` | Listeners' WhatsApp threads and what is queued for air. |
| `/admin/bulletins` | Every bulletin of the day on demand - play one from the top, or any segment, and read along. `?lang=ha` shows one language. |
| `/admin/sources` | Every aired claim with its named sources, where sources disagreed, airtime by party, and the outlet registry. |
| `/admin/rejections` | Everything the station refused to air, and why. |
| `/admin/hotlines` | Verified hotlines with the date each was checked. |
| `/admin/about` | The disclosure, the models, and the voices. |

Query parameters on the public page: `?lang=en|pcm|ha|yo|ig` picks the language, and
`?at=2026-09-19T06:05:30+01:00` pretends the page opened at that time (the build plan's S5 check).

The dial is a canvas driven by one `AnalyserNode`: the audio elements are routed through it on the
first Tune in (a browser will not start an AudioContext without a gesture), which is why they carry
`crossOrigin="anonymous"` - reading samples from the API's origin needs CORS.

## How the player stays in sync

The listener's browser and the station keep the same clock, so everyone in a language hears the same
sentence at the same moment. The player does that **without** disturbing playback:

- It syncs to the station clock once (and asks again once a minute, which never touches the audio),
  then runs the bulletin's schedule locally from its measured offsets.
- Two `<audio>` elements take turns: while one plays, the next segment is already loaded in the other,
  so a segment change is a swap rather than a download.
- Drift is taken out by nudging the playback rate by a few parts in a hundred - inaudible. Only a gap
  of more than three seconds (a sleeping tab, a new bulletin going on air) is corrected by seeking.

Pidgin has no voice model yet, so choosing it shows the bulletin's script instead of audio.
