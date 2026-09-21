"use client";

/** The newsroom side of the API. A signed token from /admin/session goes in every request. */

import { useCallback, useEffect, useState } from "react";
import { apiBase } from "./api";

const TOKEN_KEY = "raia.admin.token";

export function token(): string | null {
  return typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(value: string | null) {
  if (value) window.localStorage.setItem(TOKEN_KEY, value);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export class Unauthorised extends Error {}

export async function adminApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiBase() + "/admin" + path, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
      ...(init.headers || {}),
    },
  });
  if (response.status === 401) {
    setToken(null);
    throw new Unauthorised("Sign in to the newsroom first");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}) as { detail?: string });
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export async function signIn(username: string, password: string): Promise<string> {
  const response = await fetch(apiBase() + "/admin/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const body = await response.json().catch(() => ({}) as { detail?: string; token?: string });
  if (!response.ok || !body.token) throw new Error(body.detail || "Could not sign in");
  setToken(body.token);
  return body.token;
}

/** Fetch from the newsroom API, with `reload()` and an optional poll interval. */
export function useAdmin<T>(path: string | null, pollMs = 0): { data: T | null; error: string | null; reload: () => void; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt(n => n + 1), []);

  useEffect(() => {
    if (!path) return;
    let live = true;
    const fetchOnce = () =>
      adminApi<T>(path)
        .then(result => {
          if (!live) return;
          setData(result);
          setError(null);
        })
        .catch((err: Error) => {
          if (!live) return;
          if (err instanceof Unauthorised) window.location.href = "/admin/login";
          else setError(err.message);
        })
        .finally(() => live && setLoading(false));
    void fetchOnce();
    const timer = pollMs ? setInterval(fetchOnce, pollMs) : null;
    return () => {
      live = false;
      if (timer) clearInterval(timer);
    };
  }, [path, attempt, pollMs]);

  return { data, error, reload, loading };
}

export interface LanguageAudio {
  state: "none" | "queued" | "running" | "done" | "failed" | "stale";
  bulletin_id: string | null;
  files: number;
  duration_s: number;
  message: string | null;
  updated_at: string | null;
}

export interface Entry {
  id: string;
  date: string;
  block: string;
  title: string;
  start: string; // "06:00:00"
  ends: string; // "07:30"
  duration_min: number;
  languages: string[];
  template: string;
  presenter_primary: string;
  presenter_sport: string | null;
  origin: "clock" | "admin";
  audio: Record<string, LanguageAudio>;
}

export interface Job {
  id: string;
  kind: string;
  date: string;
  block: string | null;
  languages: string[];
  state: "queued" | "running" | "done" | "failed" | "cancelled";
  step: string | null;
  message: string | null;
  log: string[];
  started_at: string;
  finished_at: string | null;
}

export interface Options {
  languages: string[];
  templates: Record<string, string[]>;
  presenters: Record<string, { name: string; languages: string[]; voices: Record<string, string> }>;
  nightly_build_at: string;
}

export interface ScriptSegment {
  id: string;
  kind: string;
  presenter: string | null;
  script: string;
  duration: number | null;
  tts: string | null;
  audio_url: string | null;
}

export const AUDIO_TONE: Record<string, string> = {
  done: "good",
  running: "warn",
  queued: "warn",
  stale: "warn",
  failed: "bad",
  none: "",
};

/** Calendar dates are plain days, so they are counted in day parts: turning them into UTC first
 *  moves them across midnight for anyone east or west of Greenwich. */
export function shiftDay(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  return isoDay(new Date(year, month - 1, day + days));
}

export function isoDay(when: Date = new Date()): string {
  return `${when.getFullYear()}-${String(when.getMonth() + 1).padStart(2, "0")}-${String(when.getDate()).padStart(2, "0")}`;
}

/** "06:00:00" -> minutes from midnight. */
export function minutesOf(clock: string): number {
  const [h, m] = clock.split(":").map(Number);
  return h * 60 + m;
}

export function clockOf(minutes: number): string {
  const m = Math.max(0, Math.min(24 * 60 - 1, Math.round(minutes)));
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}
