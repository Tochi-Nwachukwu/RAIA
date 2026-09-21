"use client";

import { useCallback, useEffect, useState } from "react";

/** The API defaults to http://localhost:8000; ?api=https://host overrides it and is remembered. */
const DEFAULT_API = process.env.NEXT_PUBLIC_RAIA_API || "http://localhost:8000";

export function apiBase(): string {
  if (typeof window === "undefined") return DEFAULT_API;
  const asked = new URLSearchParams(window.location.search).get("api");
  if (asked) window.localStorage.setItem("raia.api", asked);
  return (window.localStorage.getItem("raia.api") || DEFAULT_API).replace(/\/$/, "");
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiBase() + path, { cache: "no-store", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}) as { detail?: string });
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

/** Fetch once (and whenever `deps` change). `null` data means still loading. */
export function useApi<T>(path: string | null, deps: unknown[] = []): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt(n => n + 1), []);

  useEffect(() => {
    if (!path) return;
    let live = true;
    setError(null);
    api<T>(path)
      .then(result => live && setData(result))
      .catch((err: Error) => live && setError(friendly(err)));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, attempt, ...deps]);

  return { data, error, reload };
}

export function friendly(err: Error): string {
  return err.message.includes("fetch")
    ? `Cannot reach the RAIA API at ${apiBase()}. Is it running? (cd backend && uv run uvicorn main:app --port 8000)`
    : err.message;
}

export const LANGUAGES: Record<string, string> = {
  en: "English",
  pcm: "Pidgin",
  ha: "Hausa",
  yo: "Yoruba",
  ig: "Igbo",
};

export const KIND: Record<string, string> = {
  station_id: "Station ID",
  disclosure: "Disclosure",
  headlines: "Headlines",
  story: "Story",
  explainer: "Explainer",
  sport: "Sport",
  listener: "Your questions",
  hotlines: "Hotlines",
  handover: "Handover",
  tease: "Coming up",
  timecheck: "Time check",
  bed: "Music bed",
};

export const STATUS: Record<string, [string, string]> = {
  corroborated: ["good", "corroborated"],
  single_source: ["warn", "one outlet only"],
  attributed: ["warn", "attributed claim"],
  disputed: ["bad", "disputed - not aired"],
  verified: ["good", "verified"],
  developing: ["warn", "developing"],
  unverified: ["bad", "unverified"],
};

export function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Wall-clock time in the station's timezone, e.g. "06:02:37". */
export function stationClock(startsAt: string, offset = 0): string {
  return new Date(Date.parse(startsAt) + offset * 1000).toLocaleTimeString("en-GB", { timeZone: "Africa/Lagos" });
}

export function minutes(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}

export function titleCase(text: string): string {
  return text.replaceAll("_", " ").replace(/^\w/, c => c.toUpperCase());
}
