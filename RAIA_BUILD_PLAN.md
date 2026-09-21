# RAIA — Radio AI Africa

**Build plan for Claude Code.** Read this in full before writing any code.

---

## 1. What this is

A civic radio station for African audiences, produced by AI agents and delivered
as a scheduled broadcast. Listeners open a page and drop into whatever is playing
right now, the same way they would tune a dial. Agents gather news and sport from
verified sources, corroborate claims, write spoken copy, translate it, and render
it to audio ahead of time. Listeners reply over WhatsApp and hear their questions
answered on air.

**The product is not the audio. The product is trust.** Every aired claim traces
back to named sources with timestamps. The station says plainly when it does not
know something. That is the differentiator; build accordingly.

Submitted to the Andela × Open Society Foundations invention sprint. Tracks:
Transparency & Accountability (primary), Stability & Social Cohesion, Safety &
Reporting.

---

## 2. Non-negotiable design rules

These are constraints, not preferences. Do not design around them.

1. **Python 3.12, uv, FastAPI, Pydantic v2. Async throughout.**
2. **No agent framework.** Plain async functions, Pydantic in and out. Do not add
   LangGraph, CrewAI, or AutoGen. If a module needs orchestration, write the
   orchestration.
3. **TTS is slow** (12–25s per sentence on M1). Synthesis never happens inside a
   request handler. The pipeline is a batch job; the API serves precomputed files.
4. **All model calls go through `src/llm.py`.** No provider SDK imported anywhere
   else. Provider and model tier are config.
5. **`src/speech/` already works.** Do not modify it. Call it.
6. **Never invent a source URL, hotline number, statistic, or quote.** Curated
   and fetched data only. If data is missing, the segment says so on air.
7. **Durations come from `ffprobe` on rendered audio, never from estimates.**
   YarnGPT samples at temperature; the same sentence is not the same length twice.
   Synthesize → measure → schedule, strictly in that order.
8. **Synthetic-voice disclosure is mandatory.** Once per hour on air, and on every
   page. Non-removable. See §7.
9. **No copyrighted music.** Station beds and stings from CC0 / explicitly licensed
   libraries only, a small fixed set, no full tracks.
10. **Every module ships with a runnable check.** Run it. Show real output. Never
    present stub data as working.

---

## 3. Repository structure

```
backend/
├── CLAUDE.md
├── pyproject.toml
├── config/
│   ├── sources.yaml             # outlet registry (§8)
│   ├── clock.yaml               # daily programme template (§6)
│   ├── presenters.yaml          # voice personas (§7)
│   ├── editorial.yaml           # editorial rules incl. election (§9)
│   └── hotlines.yaml            # curated, with verified_on dates
├── main.py                      # FastAPI app, routers only
└── src/
    ├── llm.py                   # single model gateway
    ├── models/                  # the data contract — build first
    │   ├── story.py
    │   ├── segment.py
    │   ├── bulletin.py
    │   └── listener.py
    ├── ingest/
    │   ├── registry.py
    │   ├── feeds.py             # RSS/Atom — primary path
    │   ├── article.py           # full-text fetch for truncated feeds
    │   └── scrape.py            # fallback only, robots.txt respected
    ├── desk/                    # agents that produce Story objects
    │   ├── extractor.py
    │   ├── cluster.py
    │   ├── verifier.py
    │   ├── sports.py
    │   ├── action.py
    │   └── explainer.py
    ├── studio/                  # agents that produce spoken text
    │   ├── editor.py
    │   ├── scriptwriter.py
    │   ├── continuity.py
    │   ├── translator.py
    │   └── phrasebook.py        # anti-repetition ledger
    ├── gate/
    │   └── safety.py            # final veto before synthesis
    ├── lang/
    │   └── diacritics.py        # tone marks, pre-TTS
    ├── speech/                  # EXISTING — do not modify
    ├── audio/
    │   ├── render.py            # text -> wav via speech/, content-hash cached
    │   └── assemble.py          # ffmpeg stitch, loudness normalise, beds
    ├── schedule/
    │   ├── clock.py             # loads clock.yaml
    │   └── builder.py           # pure function: segments -> offsets
    ├── pipeline/
    │   ├── morning.py
    │   ├── update.py            # midday / evening top-ups
    │   └── insert.py            # single-item breaking or listener answer
    ├── listener/
    │   └── desk.py              # WhatsApp triage
    ├── api/
    │   ├── now.py               # what is playing right now
    │   ├── bulletins.py
    │   ├── sources.py           # provenance pages
    │   ├── hotlines.py
    │   └── whatsapp.py          # webhook
    └── store/
        ├── db.py                # Beanie / MongoDB
        └── blob.py              # S3 or R2
```

---

## 4. Data contract

Build this first. Everything else is downstream. These models are the interface
between agents — agents pass typed objects, never prose.

```python
# models/story.py

class SourceRef(BaseModel):
    outlet: str
    url: HttpUrl
    published_at: datetime
    retrieved_at: datetime
    tier: Literal["official", "wire", "national", "local", "community", "factcheck"]
    language: str

class Claim(BaseModel):
    """A single assertion, separately verifiable."""
    text: str
    supported_by: list[int]          # indices into Story.sources
    status: Literal["corroborated", "single_source", "attributed", "disputed"]
    attributed_to: str | None        # who said it, if it is a claim not a fact

class Story(BaseModel):
    id: str
    headline: str
    summary: str
    claims: list[Claim]
    track: Literal["accountability", "cohesion", "safety", "sports", "health", "civic_info"]
    region: str                      # state, or "national", or country code
    sources: list[SourceRef]
    confidence: Literal["verified", "developing", "unverified"]
    sensitive: bool                  # -> human review, never straight to air
    action: str | None               # what a listener can DO about this
    action_verified: bool            # was the action checked against a real source
    first_seen: datetime
    last_updated: datetime
    aired_in: list[str]              # bulletin ids — powers back-references
```

Two fields carry the whole pitch:

- **`sources` + `claims`** are the audit trail. A story cannot reach `verified`
  without two independent outlets from different owners. The sources page renders
  directly off this.
- **`action`** is what makes it civic rather than media. If no action can be
  found, the story airs labelled as information-only. Do not fabricate actions.

```python
# models/segment.py

class Segment(BaseModel):
    id: str
    kind: Literal["station_id", "headlines", "story", "explainer", "sport",
                  "listener", "hotlines", "handover", "tease", "timecheck",
                  "disclosure", "bed"]
    language: str                    # en | pcm | yo | ig | ha | sw
    presenter: str                   # voice key from presenters.yaml
    script: str                      # spoken copy, post-diacritics
    story_id: str | None
    audio_path: str | None
    duration: float | None           # ffprobe ONLY, seconds
    cache_key: str                   # sha256(script + lang + voice)

# models/bulletin.py

class ScheduleEntry(BaseModel):
    start: time
    end: time
    segment_id: str
    audio_url: str
    kind: str
    story_id: str | None
    sources_count: int | None
    confidence: str | None

class Bulletin(BaseModel):
    id: str
    date: date
    language: str
    block: str                       # "morning_drive", "midday", etc.
    segments: list[Segment]
    schedule: list[ScheduleEntry]
    generated_at: datetime
    source_count: int
    stories_rejected: int            # transparency metric, show it on the site
```

---

## 5. The agent roster

Eleven agents. Each has one job. Model tier matters — use a cheap fast model for
high-volume extraction, an expensive one only where judgement is required.

### Desk (produce Story objects)

| Agent | Job | In → Out | Tier |
|---|---|---|---|
| `extractor` | Feed item → Story draft. Pull headline, summary, entities, track, region. | FeedItem → Story | cheap |
| `cluster` | Group drafts about the same event into one Story with many SourceRefs. Embeddings + cosine threshold, LLM tiebreak on borderline pairs only. | Story[] → Story[] | cheap |
| `verifier` | The core. Corroborate claims across outlets. Assign per-claim status and story confidence. Detect contradictions between sources. **Check recency against superseding events.** Flag `sensitive`. | Story → Story | expensive |
| `sports` | Sports desk. Different register, different sources, fixtures and results context. Keeps sport out of the news agents' way. | FeedItem[] → Story[] | cheap |
| `action` | Produce `action`: the concrete thing a listener can do. Must cite a real source (a form, an office, a deadline, a number). Returns None rather than inventing. | Story → Story | mid |
| `explainer` | For policy, budget and election items: what does this mean for an ordinary person. This agent *is* the Transparency track. | Story → str | expensive |

### Studio (produce spoken text)

| Agent | Job | In → Out | Tier |
|---|---|---|---|
| `editor` | Running order. Select, rank, balance across track and region, enforce the duration budget from `clock.yaml`, apply `editorial.yaml`. Decides what gets dropped when the block overruns. | Story[] + slot → Story[] | expensive |
| `scriptwriter` | Story[] → spoken copy in a named presenter's voice. Broadcast register: short sentences, no subordinate clauses, numbers spoken not written. Consumes `phrasebook` to avoid repeating openings. | Story[] → Segment[] | mid |
| `continuity` | **The one that makes it feel real.** Greetings, handovers, time checks, coming-up teases, back-references to previous bulletins, listener shout-outs, sign-offs, disclosure placement. See §7. | Segment[] + context → Segment[] | mid |
| `translator` | English script → pcm / yo / ig / ha. Broadcast register, not literal translation. Prefer sourcing natively-written copy where a feed offers it (§8). | Segment → Segment | expensive |
| `safety_gate` | Final veto. Runs on every script immediately before synthesis. Deterministic rule checks first, model review second. Nothing reaches TTS without passing. Logs every rejection with a reason. | Segment → Segment \| Rejection | expensive |

### Listener

| Agent | Job | In → Out | Tier |
|---|---|---|---|
| `listener_desk` | Triage inbound WhatsApp. Classify: question / report / hotline request / feedback / out-of-scope. Route. Draft reply. Queue on-air answers. Never transcribe-and-broadcast — classify-and-route. | Message → Routing | mid |

**Not agents — deterministic services:** `feeds`, `article`, `scrape`,
`diacritics` (API call), `render`, `assemble`, `schedule/builder`, `phrasebook`.
Do not put a language model anywhere near timestamp arithmetic.

---

## 6. The clock

Radio runs on a repeating hourly template. The clock defines slots; the editor
fills them. This is what separates a station from a podcast feed.

`config/clock.yaml`:

```yaml
blocks:
  morning_drive:
    start: "06:00"
    duration_min: 90
    languages: [pcm, en]
    presenter_primary: idera
    slots:
      - {kind: station_id,  budget_s: 10}
      - {kind: disclosure,  budget_s: 12}
      - {kind: headlines,   budget_s: 90,  max_stories: 5}
      - {kind: timecheck,   budget_s: 8}
      - {kind: story,       budget_s: 150, track: accountability}
      - {kind: tease,       budget_s: 15}
      - {kind: bed,         budget_s: 20}
      - {kind: story,       budget_s: 150, track: civic_info}
      - {kind: explainer,   budget_s: 210}
      - {kind: handover,    budget_s: 12}
      - {kind: sport,       budget_s: 120}
      - {kind: listener,    budget_s: 120, max_items: 3}
      - {kind: hotlines,    budget_s: 45}
      - {kind: station_id,  budget_s: 10}

  hausa_bulletin:   {start: "07:30", duration_min: 20, languages: [ha], presenter_primary: hausa_female1}
  yoruba_bulletin:  {start: "08:00", duration_min: 20, languages: [yo], presenter_primary: yoruba_female2}
  igbo_bulletin:    {start: "08:30", duration_min: 20, languages: [ig], presenter_primary: igbo_female1}
  midday_update:    {start: "13:00", duration_min: 25, languages: [pcm, en], presenter_primary: chinenye}
  evening_drive:    {start: "17:00", duration_min: 60, languages: [pcm, en], presenter_primary: zainab}
```

Outside these blocks, loop the most recent bulletin in the listener's chosen
language with a bed underneath. Never dead air.

### Playback

The morning run writes `runs/<date>/<block>/<lang>/manifest.json` containing the
schedule with absolute start offsets. The player asks the server what time it is,
finds where `now` falls, loads that file and seeks to the offset. Two listeners in
different cities hear the same sentence at the same moment.

`GET /now?lang=pcm` returns the current entry, the offset into it, and the next
three entries so the client can prefetch.

Say this in the pitch: **a shared clock is a small public commons.** That is the
name and the thesis.

---

## 7. Making it feel human

This is a deliverable, not polish. A station that reads a list of headlines and
stops is a podcast. Implement all of these in `continuity`.

1. **Named presenters, fixed slots.** `presenters.yaml` maps a voice key to a
   name, a language, a slot, and three or four verbal habits. Same voice in the
   same slot every day. Listeners attach to voices.
2. **Time-of-day awareness.** Greeting, energy and pacing differ morning vs
   evening. "It's six o'clock on a Saturday morning."
3. **Calendar awareness.** Day of week, public holidays, Ramadan, and election
   milestones from `editorial.yaml` change the copy.
4. **Handovers between presenters.** "That's the news. Chinenye, what's the sport?"
5. **Coming-up teases, then delivery.** The tease must name something that
   actually airs later in the block. Verify this programmatically.
6. **Back-references.** "You'll remember we reported on Tuesday that…" — driven by
   `Story.aired_in`. This is why previous runs live in Mongo. Nothing makes a
   station feel alive like it remembering what it said.
7. **Time checks** between segments, generated from the schedule, not guessed.
8. **Listener presence.** Real inbound questions, read on air, first name and city
   only, consent required. "Fatima in Kano asks…"
9. **Beds and stings.** Short instrumental under headlines, a sting between
   segments. CC0 or explicitly licensed. Small fixed library. No full tracks.
10. **Varied segment length.** Uniform blocks read as robotic. The clock already
    varies budgets; make the scriptwriter respect them rather than averaging.
11. **Anti-repetition ledger.** `phrasebook.py` records every opening phrase used
    in the last N bulletins and forbids reuse. Without this, every story starts
    with "In other news" by day three.
12. **Sign-post the clock, not the file.** "Coming up at half past."

### Disclosure — mandatory, and a feature

The presenters are synthetic. Say so.

- A 10–12 second disclosure segment once per hour, in the block's language.
- A permanent line on every page and every sources page.
- Named on the About page with the models used.

You are pitching a trust-and-verification project. Undisclosed synthetic
presenters is the precise failure mode the judges are convened to worry about,
and you will be asked. "We disclose every hour, in every language, and it cannot
be turned off" is a winning answer. Warm and honest is not in tension with
human-feeling — it is what human-feeling means when the voice is a machine.

---

## 8. Source registry

`config/sources.yaml`. **Claude Code must verify every feed resolves and returns
recent items. Drop any that fail and report which.** Do not assume a URL works
because it appears here.

### Tier: official
| Outlet | Notes |
|---|---|
| INEC (inecnigeria.org) | Election authority. Highest tier for anything electoral. Check for a feed; scrape the press-release page if none. |
| NCDC (ncdc.gov.ng) | Health track. |
| CBN (cbn.gov.ng) | FX rate, monetary policy. |

### Tier: national — try `/feed` (most are WordPress)
| Outlet | Feed candidate |
|---|---|
| Premium Times | `premiumtimesng.com/feed` |
| Punch | `punchng.com/feed` |
| Vanguard | `vanguardngr.com/feed` |
| The Cable | `thecable.ng/feed` |
| Daily Post | `dailypost.ng/feed` |
| Channels TV | `channelstv.com/feed` |
| Daily Trust | `dailytrust.com/feed` |
| Business Day | `businessday.ng/feed` |
| The Guardian Nigeria | `guardian.ng/feed` |
| PM News | `pmnewsnigeria.com/feed` |
| Nigerian Tribune | `tribuneonlineng.com/feed` |

### Tier: national — native-language sources ← **HIGH VALUE**
BBC's language services publish in your exact target languages. Using natively
written copy instead of machine-translating English is better quality *and* a
strong pitch point. Try the `feeds.bbci.co.uk/<service>/rss.xml` pattern:

| Service | Candidate |
|---|---|
| BBC Hausa | `feeds.bbci.co.uk/hausa/rss.xml` |
| BBC Yoruba | `feeds.bbci.co.uk/yoruba/rss.xml` |
| BBC Igbo | `feeds.bbci.co.uk/igbo/rss.xml` |
| BBC Pidgin | `feeds.bbci.co.uk/pidgin/rss.xml` |
| BBC Swahili | `feeds.bbci.co.uk/swahili/rss.xml` |
| VOA Hausa | check `voahausa.com` |

Premium Times also publishes in Hausa — check for a separate feed.

### Tier: factcheck ← **powers the verifier**
| Outlet | Notes |
|---|---|
| Dubawa (`dubawa.org`) | Nigerian fact-checking, from CJID. |
| Africa Check (`africacheck.org`) | Pan-African, has a Nigeria desk. |
| FactCheckHub (`factcheckhub.com`) | ICIR's fact-check arm. |

A story contradicted by a `factcheck`-tier source is downgraded to `disputed`
regardless of how many outlets carried it. Implement this explicitly.

### Tier: accountability ← **powers the Transparency track**
| Outlet | Notes |
|---|---|
| Dataphyte (`dataphyte.com`) | Data journalism, budgets and public spending. |
| BudgIT / Tracka | Public spending and project tracking. |
| ICIR (`icirnigeria.org`) | Investigative. |
| FIJ (`fij.ng`) | Investigative. |

### Tier: sport
Complete Sports (`completesports.com`), Soccernet NG (`soccernet.ng`),
BBC Sport Africa (`feeds.bbci.co.uk/sport/football/africa/rss.xml`).

### Tier: pan-African
AllAfrica (`allafrica.com` — has per-country RDF headline feeds),
Africanews (`africanews.com`), The Continent, Mail & Guardian.

**Rules for the registry:** each entry carries `tier`, `region`, `language`,
`owner` (for independence checks — two outlets under one owner do not corroborate
each other), and `robots_ok`. Respect robots.txt. Rate-limit per host. Cache
aggressively; you will be re-running this fifty times during the build.

---

## 9. Editorial rules

`config/editorial.yaml`. These are enforced by `editor` and vetoed by
`safety_gate`. Put them in the pitch deck verbatim — a system that has thought
about when *not* to broadcast is a serious system.

### Always
- Attribute claims. "The minister said X", never "X".
- Never air an unverified casualty figure.
- Never attribute blame to an ethnic or religious group in an unverified incident.
- Never name a suspect who has not been charged.
- Anything `sensitive: true` requires human review before synthesis. No exceptions,
  no override flag.
- If corroboration fails, the station says so on air: "We have seen this reported
  by one outlet only and have not been able to confirm it."

### Election rules (live now — campaigns opened 19 Aug and 9 Sep 2026)
- **Never project, call, or speculate on a result.**
- Results, turnout and registration figures: INEC `official` tier only.
- Campaign claims are `attributed`, never `corroborated`. A party saying a thing
  is evidence the party said it, nothing more.
- Track airtime by party across each block and log it. Imbalance is a bug.
- No personal attacks on candidates. No ethnic or religious framing of candidates.
- Violence at a campaign event → `sensitive` → human review.
- **Voter education is the backbone and is always safe to air**: how to register,
  where, what a PVC is, what the deadlines are, how to check your polling unit.
  This is the highest-value, lowest-risk civic content you have. Lead with it.

### Known election facts as of September 2026 — seed `editorial.yaml`
- Electoral Act 2022 repealed; **Electoral Act 2026** in force.
- Presidential and National Assembly election: **Saturday 16 January 2027**.
- Governorship and State Houses of Assembly: **Saturday 6 February 2027**.
- Party primaries ran 23 April – 30 May 2026.
- Presidential/NASS campaigns opened 19 August 2026.
- Governorship/State Assembly campaigns opened 9 September 2026.
- Register of voters published 15 December 2026; notice of poll 29 December 2026.
- Campaigns end 14 January 2027 (Presidential/NASS) and 4 February 2027 (Gov/State).
- Continuous Voter Registration runs April 2026 – January 2027.

### Test Case #1 — build the verifier against this
INEC revised the timetable in **February 2026**. The earlier dates were 20 February
and 6 March 2027. **Articles published before the revision are still online and
still carry the old dates.**

An agent that ranks by relevance without checking recency will broadcast the wrong
election date to people planning to vote. The verifier must catch this: when
sources disagree on a date, the `official` tier wins, and among non-official
sources the later `published_at` wins, and the contradiction is logged and shown
on the sources page.

Make this a fixture test with real archived URLs. Then put it in the demo video.
It shows the entire value proposition in thirty seconds.

---

## 10. Pipeline

`uv run python -m src.pipeline.morning --date 2026-09-19 --lang pcm,en,ha,yo,ig`

Stages, each checkpointing to `runs/<date>/`:

1. **ingest** — fetch all feeds concurrently, per-source error isolation (one dead
   feed must never kill a run). Write `raw.jsonl`.
2. **extract** — feed items → Story drafts. Batch. Cheap model.
3. **cluster** — collapse duplicates into multi-source Stories.
4. **verify** — corroborate, assign confidence, detect contradictions, check
   recency, flag sensitive. Write `stories.json`.
5. **enrich** — `action` and `explainer` on selected stories only, after the editor
   has picked them. Do not enrich stories that will not air; it wastes tokens.
6. **edit** — running order per block per language against `clock.yaml` budgets.
7. **script** — spoken copy, presenter voice, phrasebook applied.
8. **continuity** — greetings, handovers, teases, time checks, back-references,
   disclosure insertion.
9. **translate** — per target language, preferring natively-sourced copy.
10. **diacritics** — tone marks restored. **Required before TTS or Yoruba output
    will be wrong.**
11. **gate** — safety veto. Rejections logged with reasons to `rejected.json`.
12. **render** — TTS per segment, cached on `sha256(script + lang + voice)`.
    Station IDs, disclosures, hotline readouts and stings synthesize once, ever.
13. **measure** — `ffprobe` every file. Real durations only.
14. **assemble** — ffmpeg stitch, loudness normalise, beds and stings.
15. **schedule** — pure function, offsets from measured durations.
16. **publish** — write `manifest.json`, upload audio, upsert to Mongo.

Every stage is resumable from the previous stage's checkpoint. You will be
re-running stage 12 constantly and you must not re-run stages 1–11 to do it.

**Run it nightly via cron from the night before the demo.** A known-good bulletin
sitting on disk is worth more than a live run during filming.

---

## 11. Storage

**MongoDB via Beanie** (async ODM on Pydantic v2 — your models become documents
directly, no translation layer).

Collections:
- `stories` — the corpus. Indexed on `published_at`, cluster key, `region`.
  Enables back-references and cross-run dedupe.
- `bulletins` — metadata and schedules.
- `listener_messages` — **TTL index, few-day expiry.** Phone numbers hashed as
  `_id`, never stored raw.
- `hotlines` — curated, each with `verified_on`.
- `airtime_log` — party mentions per block, for the election balance check.

**Audio never goes in Mongo.** S3 or R2 holds files; Mongo holds the key. No GridFS.

**The filesystem stays authoritative for runs.** `runs/<date>/` is reproducible
and diffable. Mongo holds what the pipeline does not own.

Say the TTL index out loud in the pitch. It turns "we respect privacy" from a
promise into an enforced property of the schema — the data is gone whether or not
anyone remembers to delete it.

---

## 12. Build order

One session per step. Do not start the next until the current one's check passes
with real output.

**S1 — Contract + ingest.** `models/`, `ingest/registry.py`, `ingest/feeds.py`,
dedupe. Verify every feed in §8 resolves; report the dead ones. Check: fetch live,
print item counts per source and survivors after dedupe.

**S2 — Verifier + editor.** `ultrathink` on this one; it is the hard design work.
Build Test Case #1 from §9 as a fixture. Check: feed it contradicting sources and
show the correct date winning with the contradiction logged.

**S3 — Scriptwriter + continuity + phrasebook.** Check: generate a full morning
block as text, in English, and read it aloud yourself. If it sounds like a list,
it is not done.

**S4 — Render + measure + assemble + schedule.** Wire in existing `speech/`.
Check: produce a real `runs/<date>/` with playable audio and a manifest whose
offsets match `ffprobe` exactly.

**S5 — API + player.** `/now`, `/bulletins`, `/sources`, `/hotlines`. Minimal
web player that seeks by wall-clock. Check: open at an arbitrary time and land
mid-segment correctly.

**S6 — Translation + diacritics + multilingual blocks.** Check: Hausa, Yoruba and
Igbo bulletins render and a native speaker confirms they are intelligible.

**S7 — WhatsApp + listener desk.** Webhook, triage, hotline replies, on-air queue.
Check: send a real message, get a routed reply, see the question queued.

**S8 — Safety gate + hardening.** Gate on everything. Rejection log visible on the
site. Check: try to push a sensitive story through and watch it fail.

---

## 13. Demo day

- The sources page is the star. Lead the video with it, not the player.
- Show the rejection log. "Here are eleven stories we refused to air, and why."
- Show `stories_rejected` on the front page as a live counter.
- Show Test Case #1 catching the stale election date.
- Show one WhatsApp message arriving and being answered on air.
- Play thirty seconds of Pidgin and thirty of Hausa. Let the audio speak.
- Record against a known-good run. Never film a live pipeline.
