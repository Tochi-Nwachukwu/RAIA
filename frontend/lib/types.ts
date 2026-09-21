// The shapes the RAIA API returns (backend/src/api/). Only the fields the site uses.

export type Language = "en" | "pcm" | "ha" | "yo" | "ig";

export interface BulletinSummary {
  id: string;
  block: string;
  language: string;
  date: string;
  starts_at: string;
  duration: number;
  source_count: number;
  stories_rejected: number;
  audio_available: boolean;
  tts_note: string | null;
}

export interface ScheduleEntry {
  start: string;
  end: string;
  segment_id: string;
  audio_url: string;
  kind: string;
  story_id: string | null;
  sources_count: number | null;
  confidence: string | null;
  offset: number; // seconds from the block start
  duration: number; // ffprobe seconds of the file at audio_url
}

export interface Segment {
  id: string;
  kind: string;
  language: string;
  presenter: string;
  script: string;
  duration: number | null;
  tts: string;
  story_id: string | null;
  story_ids: string[];
}

export interface Bulletin extends BulletinSummary {
  segments: Segment[];
  schedule: ScheduleEntry[];
  generated_at: string;
}

/** A schedule entry as /now returns it: with the segment's script and presenter. */
export interface NowEntry extends ScheduleEntry {
  script?: string;
  presenter?: string;
  story_ids?: string[];
  position?: number; // seconds into this entry
}

export interface NowResponse {
  server_time: string;
  timezone: string;
  language: string;
  disclosure: string;
  mode: "live" | "loop" | "text";
  bulletin: BulletinSummary;
  current?: NowEntry;
  next?: NowEntry[];
  segments?: { id: string; kind: string; script: string }[]; // text-only bulletins
  bed_url?: string | null;
}

export interface Presenter {
  name: string;
  languages: string[];
  slots: string[];
}

export interface Station {
  name: string;
  disclosure: string;
  models: Record<string, string[]>;
  models_used?: { date: string; calls: Record<string, { calls: number; input: number; output: number }> };
  llm_provider: string;
  embeddings: string;
  tts: Record<string, { engine: string; voice_model: string | null; note: string | null }>;
  languages: Record<string, string>;
  presenters: Record<string, Presenter>;
}

export interface ClaimSource {
  outlet: string;
  url: string;
  published_at: string;
  tier?: string;
}

export interface Claim {
  text: string;
  status: string;
  attributed_to: string | null;
  sources: ClaimSource[];
}

export interface StoryContradiction {
  subject: string;
  resolved_value: string;
  rule: string;
  values: { value: string; sources: ClaimSource[] }[];
}

export interface Story {
  id: string;
  headline: string;
  summary: string;
  track: string;
  region: string;
  confidence: string;
  sources: ClaimSource[];
  owners: string[];
  claims: Claim[];
  contradictions: StoryContradiction[];
  aired_in: string[];
  action: string | null;
  action_source: string | null;
  action_verified: boolean;
}

export interface SourceRow {
  outlet: string;
  tier: string;
  owner: string;
  language: string;
  homepage: string;
  verified_on: string | null;
}

export interface Rejection {
  block: string;
  language: string;
  stage: string;
  rule: string;
  reason: string;
  story_id: string | null;
  segment_id: string | null;
  headline: string | null;
}

export interface Hotline {
  id: string;
  service: string;
  numbers: string[];
  purpose: string;
  source_url: string;
  source_name: string;
  hours: string | null;
  note: string | null;
  verified_on: string;
}

export interface AirtimeBulletin {
  bulletin: string;
  audio_available: boolean;
  parties: Record<string, number>;
  imbalance: boolean;
}
