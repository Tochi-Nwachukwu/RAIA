"use client";

/**
 * The station player's engine.
 *
 * It syncs to the station clock ONCE, then runs the bulletin's schedule locally: the next segment is
 * loaded into a second <audio> element while the current one plays, so a segment change is a swap,
 * not a download. Nothing seeks during playback. Small drift (the listener's clock against the
 * station's) is taken out by nudging the playback rate by a few parts in a hundred, which is
 * inaudible; only a gap of more than a few seconds - a sleeping tab, a new bulletin going on air -
 * is corrected by jumping.
 *
 * ?at=2026-09-19T06:05:30+01:00 pretends the page opened at that time (the build plan's S5 check).
 */

import { type RefObject, useCallback, useEffect, useRef, useState } from "react";
import { api, friendly } from "@/lib/api";
import type { Bulletin, NowResponse, ScheduleEntry } from "@/lib/types";

const ASK_SERVER_MS = 60_000; // how often we ask what is on air (this never touches the audio)
const HARD_RESYNC_S = 3; // drift we jump rather than nudge away
const MAX_NUDGE = 0.04; // 4% playback-rate change: inaudible, and takes out 3s of drift in ~75s

export type Mode = "live" | "loop";
export type Timeline = { mode: Mode; schedule: ScheduleEntry[]; index: number; offset: number };
type Playing = { mode: Mode; schedule: ScheduleEntry[]; index: number };

/** A replay leaves out time checks - they would say the wrong time - and closes the gaps. */
function loopSchedule(schedule: ScheduleEntry[]): ScheduleEntry[] {
  let offset = 0;
  return schedule
    .filter(e => e.kind !== "timecheck")
    .map(e => {
      const entry = { ...e, offset };
      offset += e.duration;
      return entry;
    });
}

/** Where the station is in this bulletin at `atMs`: the same arithmetic the server does. */
export function timelineAt(bulletin: Bulletin, atMs: number): Timeline | null {
  if (!bulletin.schedule.length) return null;
  const elapsed = (atMs - Date.parse(bulletin.starts_at)) / 1000;
  if (elapsed < 0) return null;
  const liveTotal = bulletin.schedule.reduce((sum, e) => sum + e.duration, 0);
  let mode: Mode = "live";
  let schedule = bulletin.schedule;
  let point = elapsed;
  if (elapsed >= liveTotal) {
    mode = "loop";
    schedule = loopSchedule(bulletin.schedule);
    const loopTotal = schedule.reduce((sum, e) => sum + e.duration, 0);
    if (loopTotal <= 0) return null;
    point = (elapsed - liveTotal) % loopTotal;
  }
  const found = schedule.findIndex(e => point < e.offset + e.duration);
  const index = found < 0 ? schedule.length - 1 : found;
  return { mode, schedule, index, offset: Math.max(0, point - schedule[index].offset) };
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** Resolve once the element has reached `readyState`, or after 3 seconds, whichever comes first. */
function waitFor(element: HTMLAudioElement, event: "loadedmetadata" | "canplay", readyState: number): Promise<void> {
  if (element.readyState >= readyState) return Promise.resolve();
  return new Promise<void>(resolve => {
    const done = () => {
      clearTimeout(timer);
      element.removeEventListener(event, done);
      resolve();
    };
    const timer = setTimeout(done, 3000);
    element.addEventListener(event, done);
  });
}

export interface StationPlayer {
  lang: string;
  setLanguage: (next: string) => void;
  now: NowResponse | null;
  bulletin: Bulletin | null;
  view: Timeline | null;
  entry: ScheduleEntry | null;
  script: string;
  presenterKey: string;
  playing: boolean;
  progress: number;
  error: string | null;
  textOnly: boolean;
  simulatedTime: number | null;
  tuneIn: () => Promise<void>;
  stop: () => void;
  audios: RefObject<HTMLAudioElement | null>[];
  beds: RefObject<HTMLAudioElement | null>[];
  onEnded: () => void;
  onBedEnded: () => void;
}

export function useStationPlayer(): StationPlayer {
  const [lang, setLang] = useState("en");
  const [now, setNow] = useState<NowResponse | null>(null);
  const [bulletin, setBulletin] = useState<Bulletin | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [view, setView] = useState<Timeline | null>(null);
  const [progress, setProgress] = useState(0);
  const [simulatedTime, setSimulatedTime] = useState<number | null>(null);

  const audios = [useRef<HTMLAudioElement>(null), useRef<HTMLAudioElement>(null)];
  const beds = [useRef<HTMLAudioElement>(null), useRef<HTMLAudioElement>(null)];
  const bedActive = useRef(0);
  const bedWanted = useRef(false);
  const active = useRef(0);
  const played = useRef<Playing | null>(null);
  const skew = useRef(0); // station clock minus this browser's clock, in ms
  const simulated = useRef<{ from: number; opened: number } | null>(null);
  const playingRef = useRef(false);
  const bulletinRef = useRef<Bulletin | null>(null);

  /** The station's clock, as this page reckons it. */
  const stationNow = useCallback(() => {
    const sim = simulated.current;
    return sim ? sim.from + (Date.now() - sim.opened) : Date.now() + skew.current;
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const at = params.get("at");
    if (at && !Number.isNaN(Date.parse(at))) {
      simulated.current = { from: Date.parse(at), opened: Date.now() };
      setSimulatedTime(Date.parse(at));
    }
    setLang(params.get("lang") || window.localStorage.getItem("raia.lang") || "en");
  }, []);

  // What is on air: asked now, and once a minute after that. It never touches playback; it only
  // notices that a different bulletin has started, or corrects the clock.
  useEffect(() => {
    let live = true;
    const ask = async () => {
      const sim = simulated.current;
      const at = sim ? `&at=${encodeURIComponent(new Date(stationNow()).toISOString())}` : "";
      const sent = Date.now();
      try {
        const fresh = await api<NowResponse>(`/now?lang=${lang}${at}`);
        if (!live) return;
        const roundTrip = (Date.now() - sent) / 2;
        if (!sim) skew.current = Date.parse(fresh.server_time) - (Date.now() - roundTrip);
        setError(null);
        setNow(fresh);
      } catch (err) {
        if (live) setError(friendly(err as Error));
      }
    };
    void ask();
    const timer = setInterval(ask, ASK_SERVER_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [lang, stationNow]);

  // The bulletin's own schedule, fetched once per bulletin. Everything below runs off this.
  useEffect(() => {
    if (!now || now.mode === "text" || !now.bulletin) return;
    if (bulletin?.id === now.bulletin.id) return;
    let live = true;
    api<Bulletin>(`/bulletins/${encodeURIComponent(now.bulletin.id)}`)
      .then(full => {
        if (!live) return;
        bulletinRef.current = full;
        setBulletin(full);
        setView(timelineAt(full, stationNow()));
      })
      .catch(err => live && setError(friendly(err as Error)));
    return () => {
      live = false;
    };
  }, [now, bulletin?.id, stationNow]);

  /** Put the next entry in the idle element (if it is not already there) and buffer it. */
  const preload = useCallback((schedule: ScheduleEntry[], index: number) => {
    const element = audios[1 - active.current].current;
    const entry = schedule[(index + 1) % schedule.length];
    if (!element || !entry) return;
    if (element.dataset.url !== entry.audio_url) {
      element.pause();
      element.crossOrigin = "anonymous"; // the visualiser reads the samples
      element.src = entry.audio_url;
      element.dataset.url = entry.audio_url;
      element.load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const playEntry = useCallback(
    async (mode: Mode, schedule: ScheduleEntry[], index: number, offset: number, reaim = true) => {
      const element = audios[active.current].current;
      const entry = schedule[index];
      if (!element || !entry) return;
      if (element.dataset.url !== entry.audio_url) {
        element.crossOrigin = "anonymous";
        element.src = entry.audio_url;
        element.dataset.url = entry.audio_url;
        element.load();
      }
      await waitFor(element, "loadedmetadata", 1);
      if (Math.abs(element.currentTime - offset) > 0.25) element.currentTime = offset;
      element.playbackRate = 1;
      // Loading takes a moment, and the station does not wait: aim again once the file can play, so
      // the listener starts in sync instead of starting behind and being jumped forward.
      if (reaim) {
        await waitFor(element, "canplay", 3);
        const current = bulletinRef.current;
        const at = current ? timelineAt(current, stationNow()) : null;
        if (at && at.mode === mode && at.index === index && Math.abs(element.currentTime - at.offset) > 0.4) {
          element.currentTime = at.offset;
        }
      }
      played.current = { mode, schedule, index };
      setView({ mode, schedule, index, offset });
      audios[1 - active.current].current?.pause();
      try {
        await element.play();
      } catch {
        /* the browser refused to start without a gesture; the Tune in button will retry */
      }
      preload(schedule, index);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [preload, stationNow],
  );

  const stop = useCallback(() => {
    playingRef.current = false;
    setPlaying(false);
    audios.forEach(ref => ref.current?.pause());
    bedWanted.current = false;
    beds.forEach(ref => ref.current?.pause());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const tuneIn = useCallback(async () => {
    const current = bulletinRef.current;
    if (!current) return;
    const at = timelineAt(current, stationNow());
    if (!at) return;
    playingRef.current = true;
    setPlaying(true);
    await playEntry(at.mode, at.schedule, at.index, at.offset);
  }, [playEntry, stationNow]);

  // A segment finished: swap to the element that has already buffered the next one.
  const onEnded = useCallback(async () => {
    const current = bulletinRef.current;
    const last = played.current;
    if (!playingRef.current || !current || !last) return;
    const at = timelineAt(current, stationNow());
    if (!at) return stop();
    const next = (last.index + 1) % last.schedule.length;
    const continues = at.mode === last.mode && at.index === next && at.offset < 1;
    active.current = 1 - active.current;
    if (continues) await playEntry(last.mode, last.schedule, next, 0, false);
    else await playEntry(at.mode, at.schedule, at.index, at.offset);
  }, [playEntry, stationNow, stop]);

  // Four times a second: move the progress bar, and keep the drift down without seeking.
  useEffect(() => {
    const tick = () => {
      const element = audios[active.current].current;
      const current = bulletinRef.current;
      const last = played.current;
      if (!element) return;
      if (element.duration) setProgress((100 * element.currentTime) / element.duration);
      if (!playingRef.current || !current || !last || element.paused) return;
      const at = timelineAt(current, stationNow());
      if (!at) return;
      if (at.mode === last.mode && at.index === last.index) {
        const drift = element.currentTime - at.offset; // positive: we are ahead of the station
        if (Math.abs(drift) > HARD_RESYNC_S) void playEntry(at.mode, at.schedule, at.index, at.offset);
        else element.playbackRate = clamp(1 - drift * 0.03, 1 - MAX_NUDGE, 1 + MAX_NUDGE);
        return;
      }
      // The station is in a different segment. One step ahead is the normal boundary, and `ended`
      // handles it; anything else means we fell behind (a sleeping tab) and must catch up.
      const oneStep = at.mode === last.mode && at.index === (last.index + 1) % last.schedule.length;
      if (!oneStep) void playEntry(at.mode, at.schedule, at.index, at.offset);
    };
    const timer = setInterval(tick, 250);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playEntry, stationNow]);

  // The station bed plays under a replay, not under the live airing. Two elements again: looping one
  // element makes the browser re-buffer at the wrap, which is audible under quiet music.
  const onBedEnded = useCallback(() => {
    if (!bedWanted.current) return;
    bedActive.current = 1 - bedActive.current;
    const element = beds[bedActive.current].current;
    if (!element) return;
    element.currentTime = 0;
    element.volume = 0.12;
    void element.play().catch(() => {});
    const spent = beds[1 - bedActive.current].current;
    if (spent) spent.currentTime = 0; // rewind now, so its turn in 30 seconds starts from a full buffer
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const url = now?.bed_url;
    bedWanted.current = Boolean(playing && view?.mode === "loop" && url);
    if (!bedWanted.current) {
      beds.forEach(ref => ref.current?.pause());
      return;
    }
    beds.forEach(ref => {
      const element = ref.current;
      if (element && element.dataset.url !== url) {
        element.crossOrigin = "anonymous";
        element.src = url!;
        element.dataset.url = url!;
        element.load();
      }
      if (element) element.volume = 0.12;
    });
    const element = beds[bedActive.current].current;
    if (element?.paused) void element.play().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, view?.mode, now?.bed_url]);

  const setLanguage = useCallback(
    (next: string) => {
      window.localStorage.setItem("raia.lang", next);
      stop();
      played.current = null;
      audios.forEach(ref => {
        if (ref.current) {
          ref.current.removeAttribute("src");
          delete ref.current.dataset.url;
        }
      });
      setBulletin(null);
      bulletinRef.current = null;
      setView(null);
      setNow(null);
      setLang(next);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [stop],
  );

  const entry = view ? view.schedule[view.index] : null;
  const segment = bulletin?.segments.find(s => s.id === entry?.segment_id);

  return {
    lang,
    setLanguage,
    now,
    bulletin,
    view,
    entry,
    script: segment?.script || "",
    presenterKey: segment?.presenter || "",
    playing,
    progress,
    error,
    textOnly: now?.mode === "text",
    simulatedTime,
    tuneIn,
    stop,
    audios,
    beds,
    onEnded,
    onBedEnded,
  };
}
