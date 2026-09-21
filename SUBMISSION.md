# RAIA — Radio AI Africa · written summary

**A civic radio station produced by AI agents, where every sentence that goes on air traces back to a
named source you can check — and everything the station refused to air is published beside what it
did.**

Repository: this repo · Run it: see [README.md](README.md)

## 1. Track

**Primary: Transparency & Accountability.** RAIA makes institutional decisions audible and checkable
for people who will never read a PDF from a ministry. Each aired claim carries its outlets and
timestamps on a public Sources page; where outlets disagreed, the disagreement and the rule that
resolved it are published; where the station refused to speak, the refusal and its reason are
published too.

It also lands in the other two tracks by design:

- **Stability & Social Cohesion** — the election rules are enforced in code: no projecting results,
  party claims are attributed rather than stated, figures come from INEC or do not air, and airtime
  per party is measured and shown so imbalance is visible rather than hidden. Bulletins run in
  Hausa, Yoruba, Igbo and Nigerian Pidgin, not only English.
- **Safety, Reporting & Protection** — every hotline read on air was first confirmed on the agency's
  own page (11 of 14 passed; 3 are not aired). Listeners reach the station on WhatsApp, and their
  phone numbers are never stored — only a salted hash, on a record that expires.

## 2. Information sources

**28 verified outlets, four tiers, six languages.** Nothing is in the list until the pipeline has
fetched it live, parsed it, and found recent dated items.

| Tier | Outlets |
|---|---|
| Official (2) | INEC, Central Bank of Nigeria |
| Wire / pan-African (4) | AllAfrica, Africanews, The Continent, Mail & Guardian |
| National (20) | Premium Times, Punch, Vanguard, Daily Post, Channels TV, Daily Trust, BusinessDay, PM News, Nigerian Tribune, Dataphyte, ICIR, Complete Sports, Soccernet NG, BBC Sport Africa, and BBC Hausa, Yoruba, Igbo, Pidgin and Swahili, plus Premium Times Hausa |
| Fact-check (2) | Dubawa, FactCheckHub |

**Seven candidates were dropped, each with a recorded reason** — TheCable, The Guardian Nigeria, FIJ
and Africa Check block automated fetches (HTTP 403); VOA Hausa, BudgIT and NCDC had nothing recent
enough to air. The registry keeps the reason next to the outlet, and the site publishes it.

How they are used:

- **Owners, not URLs.** Every outlet records its owner, because corroboration means independence:
  two mastheads under one owner never confirm each other.
- **Tiers carry authority.** For dates, an official source outranks a national one; for figures, no
  official source means no figure airs.
- **Native-language sources are preferred material.** When BBC Hausa carries a story, its wording
  is given to the translator as a vocabulary reference for the same facts.
- **Fetching is polite.** robots.txt is honoured (RFC 9309), with per-host rate limits, a declared
  user agent, and a disk cache so a source is not hit twice for the same page. One dead feed never
  kills a run.
- **Attribution, not replacement.** Every outlet is named on air and linked on the site.

## 3. Trust and accuracy

The question this project is built around is not *can a model write a bulletin* — it obviously can —
but **what has to be true in the code before a machine is allowed to say something out loud.**
Eleven answers, each of which is a mechanism rather than a promise:

1. **Corroboration requires independent owners.** A single-outlet story may still air, but it must
   say so, word for word: *"We have seen this reported by one outlet only and have not been able to
   confirm it."*
2. **A language model never settles a contradiction.** Deterministic rules do: a settled fact beats
   an official source, which beats the later report — for dates. For figures with no official
   source, the contradiction is marked unresolved and **neither number airs**.
3. **The stale-fact test is real, not synthetic.** When INEC moved the 2027 elections from February
   to January, articles with the old dates stayed online. The verifier is tested against those
   actual archived pages and must air January.
4. **A safety gate vetoes copy before synthesis** — deterministic rules (casualty figures, blame,
   result projections, phone numbers, URLs) plus a model review that reads the source excerpts.
   Anything sensitive waits for a human approval, and there is no override flag.
5. **Refusals are published.** The rejection log is a page, not a private metric.
6. **Hotlines are verified before they can be read**, each with the date it was checked; the three
   that failed are listed as not aired.
7. **Timings are measured, never estimated.** Durations come from `ffprobe` on the rendered file,
   and the schedule is pure arithmetic no model touches.
8. **Numbers survive translation.** A second model reading checks every number in the Yoruba, Igbo
   and Hausa copy against the English; a translation that gets one wrong is retried, and one that
   still cannot be trusted stays in English rather than air a wrong figure.
9. **Election airtime is counted**, per sentence, from measured speech time, and shown per party.
10. **The presenters are synthetic and the station says so** — once an hour in every language, on
    every page, and it cannot be switched off.
11. **Listeners stay private.** Phone numbers exist only as salted hashes; threads expire on a
    database TTL; a question airs only with consent, and only as a first name and a city.

43 automated tests cover the verifier (including the archived-pages case), the gate, the schedule
arithmetic, airtime counting, the listener desk and spoken-copy hygiene.

**What we did not solve.** Nigerian Pidgin and Swahili have no voice model yet, so those bulletins
publish as text. No native speaker has yet confirmed the Hausa, Yoruba and Igbo audio — we measured
English intelligibility with an ASR check (about 80% of words recovered; proper nouns suffer most)
and used it to choose voices, but that is a proxy, not a speaker. Saying so is part of the design.

## 4. How we used AI tools

**To build it.** The entire system was written in an agentic session with Claude Code (Opus 5 and
Sonnet 5) — architecture, pipeline, verifier rules, the FastAPI service, the Next.js site, the
tests. The work was driven by a written build plan with its own success checks, and the agent was
held to evidence: every check was run against live sources and real audio rather than accepted on
assertion. Several bugs in this submission were found exactly that way — a drag that moved a
bulletin 6.5 hours instead of one, edits that could land on the wrong bulletin, day arrows that
failed for anyone east of Greenwich, a cached YAML mutation that silently gave bulletins the wrong
shape.

**Inside the product**, models do the judgement-shaped work and nothing else:

| Job | Model |
|---|---|
| High-volume extraction of story drafts | Claude Haiku 4.5 |
| Spoken copy, continuity, the listener desk | Claude Sonnet 5 |
| Verification, editing, translation, safety review | Claude Opus 5, falling back to Opus 4.8 |
| Grouping reports of the same event | OpenAI `text-embedding-3-small` |
| Nigerian-accented speech | [YarnGPT2](https://huggingface.co/saheedniyi/YarnGPT2) (open weights) |
| Choosing voices | OpenAI `whisper-1`, used as a measurement tool: the same copy rendered in eight voices, scored by word error rate |

Every model call goes through a single gateway module, so the provider and the model for each job
stay configuration rather than code. And the boundaries are explicit: **a model never resolves a
contradiction, never touches a timestamp, never invents a number, a quote or a hotline, and can
never override the safety gate.** That division — models for language, code for facts — is the whole
architecture in one sentence.
