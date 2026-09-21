"use client";

/** What the newsroom does to one bulletin: move it, change its languages, hand it to the agents,
 *  listen to what came back, and edit what the presenter says. */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { adminApi, AUDIO_TONE, clockOf, minutesOf, type Entry, type Options, type ScriptSegment } from "@/lib/admin";
import { KIND, LANGUAGES } from "@/lib/api";

interface Props {
  entry: Entry;
  options: Options | null;
  busy: boolean;
  onChanged: () => void;
  onClosed: () => void;
}

export function EntryPanel({ entry, options, busy, onChanged, onClosed }: Props) {
  // The panel is keyed on the entry, so `draft` always belongs to the bulletin on screen: a half-typed
  // title can never be saved onto the next one the newsroom clicks.
  const [draft, setDraft] = useState(entry);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [scriptsFor, setScriptsFor] = useState<string | null>(null);

  useEffect(() => {
    if (draft.id !== entry.id) setDraft(entry);
  }, [entry, draft.id]);

  // A title typed here belongs to this bulletin even if the newsroom clicks away mid-word: the edit
  // is saved against the id it was typed under, never against whatever is selected next.
  const typed = useRef({ id: entry.id, title: entry.title, was: entry.title });
  typed.current = { id: entry.id, title: draft.id === entry.id ? draft.title : entry.title, was: entry.title };
  useEffect(
    () => () => {
      const at = typed.current; // read at unmount, not at mount: this is the last thing typed
      const title = at.title.trim();
      if (title && title !== at.was) {
        void adminApi(`/entries/${at.id}`, { method: "PATCH", body: JSON.stringify({ title }) }).then(onChanged).catch(() => {});
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const patch = async (changes: Partial<Entry>) => {
    if (draft.id !== entry.id) return; // never write one bulletin's edits onto another
    setSaving(true);
    setError(null);
    try {
      await adminApi(`/entries/${entry.id}`, { method: "PATCH", body: JSON.stringify(changes) });
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const generate = async (languages?: string[]) => {
    setError(null);
    try {
      const query = languages ? `?languages=${languages.join(",")}` : "";
      await adminApi(`/entries/${entry.id}/generate${query}`, { method: "POST" });
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const remove = async () => {
    if (!confirm(`Take "${entry.title}" off the schedule?`)) return;
    await adminApi(`/entries/${entry.id}`, { method: "DELETE" }).catch(err => setError((err as Error).message));
    onClosed();
    onChanged();
  };

  const toggleLanguage = (language: string) => {
    const languages = draft.languages.includes(language)
      ? draft.languages.filter(l => l !== language)
      : [...draft.languages, language];
    if (!languages.length) return;
    setDraft({ ...draft, languages });
    void patch({ languages });
  };

  return (
    <aside className="panel">
      <div className="toolbar" style={{ margin: 0 }}>
        <strong style={{ fontSize: 17 }}>{entry.title}</strong>
        <div className="spacer" />
        <button className="small" onClick={onClosed} aria-label="close">
          ✕
        </button>
      </div>
      <p className="src" style={{ marginTop: 4 }}>
        {clockOf(minutesOf(entry.start))} - {entry.ends} · {entry.template.replace("_", " ")} ·{" "}
        {options?.presenters[entry.presenter_primary]?.name || entry.presenter_primary}
        {entry.origin === "admin" ? " · added by the newsroom" : ""}
      </p>

      <label className="field">
        <span>Title</span>
        <input
          value={draft.title}
          onChange={e => setDraft({ ...draft, title: e.target.value })}
          onBlur={() => draft.title !== entry.title && draft.title.trim() && patch({ title: draft.title.trim() })}
          onKeyDown={e => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        />
      </label>
      <div className="pickers" style={{ marginBottom: 14 }}>
        {(options ? options.languages : Object.keys(LANGUAGES)).map(language => (
          <button
            key={language}
            className={`picker${draft.languages.includes(language) ? " on" : ""}`}
            onClick={() => toggleLanguage(language)}
            disabled={saving}
          >
            {LANGUAGES[language] || language}
          </button>
        ))}
      </div>

      <h2 style={{ marginTop: 6 }}>Audio</h2>
      <table>
        <tbody>
          {draft.languages.map(language => {
            const state = entry.audio[language];
            return (
              <tr key={language}>
                <td style={{ width: 78 }}>{LANGUAGES[language] || language}</td>
                <td>
                  <span className={`chip ${AUDIO_TONE[state?.state || "none"]}`}>{state?.state || "not made yet"}</span>
                  {state?.state === "done" && state.files ? (
                    <div className="src">
                      {state.files} files · {Math.round(state.duration_s / 60)} min{" "}
                      <Link href={`/admin/bulletins?lang=${language}#${state.bulletin_id}`}>listen back</Link>
                    </div>
                  ) : null}
                  {state?.message ? <div className="src">{state.message}</div> : null}
                </td>
                <td style={{ width: 92 }}>
                  <button className="small" onClick={() => generate([language])} disabled={busy}>
                    generate
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="toolbar" style={{ marginTop: 14 }}>
        <button className="small go" onClick={() => generate()} disabled={busy}>
          Generate audio
        </button>
        <button className="small" onClick={() => setScriptsFor(draft.languages[0])}>
          View content / script
        </button>
        <div className="spacer" />
        <button className="small danger" onClick={remove}>
          Remove
        </button>
      </div>
      {busy ? <p className="src">The agents are working on another bulletin; this one will start when they are free.</p> : null}
      {error ? <p className="error">{error}</p> : null}

      {scriptsFor ? (
        <ScriptSheet entry={entry} language={scriptsFor} onLanguage={setScriptsFor} onClose={() => setScriptsFor(null)} onChanged={onChanged} />
      ) : null}
    </aside>
  );
}

function ScriptSheet({
  entry,
  language,
  onLanguage,
  onClose,
  onChanged,
}: {
  entry: Entry;
  language: string;
  onLanguage: (language: string) => void;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [data, setData] = useState<{ segments: ScriptSegment[]; source: string | null; note?: string } | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setData(null);
    adminApi<{ segments: ScriptSegment[]; source: string | null; note?: string }>(
      `/entries/${entry.id}/script?language=${language}`,
    )
      .then(setData)
      .catch(err => setError((err as Error).message));
  };
  useEffect(load, [entry.id, language]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (segment: ScriptSegment) => {
    try {
      await adminApi(`/entries/${entry.id}/script`, {
        method: "PUT",
        body: JSON.stringify({ language, segment_id: segment.id, script: text }),
      });
      setEditing(null);
      load();
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="sheet" onClick={onClose}>
      <div className="sheet-card" onClick={e => e.stopPropagation()} style={{ width: "min(760px, 100%)" }}>
        <div className="toolbar" style={{ marginTop: 0 }}>
          <h2 style={{ margin: 0 }}>{entry.title}</h2>
          <div className="spacer" />
          <div className="tabs">
            {entry.languages.map(code => (
              <button key={code} className={code === language ? "on" : ""} onClick={() => onLanguage(code)}>
                {LANGUAGES[code] || code}
              </button>
            ))}
          </div>
          <button className="small" onClick={onClose}>
            ✕
          </button>
        </div>
        {error ? <p className="error">{error}</p> : null}
        {!data ? <p className="muted">Loading the script…</p> : null}
        {data?.note ? <p className="muted">{data.note}</p> : null}
        {data?.source ? <p className="src">From {data.source}. Editing a script makes its audio stale: generate it again to hear the change.</p> : null}
        {data?.segments.map(segment => (
          <div className="segment" key={segment.id}>
            <div className="meta">
              <strong className="ink">{KIND[segment.kind] || segment.kind}</strong>
              {segment.duration ? ` · ${Math.round(segment.duration)}s` : ""}
              {segment.presenter ? ` · ${segment.presenter}` : ""}
            </div>
            {segment.audio_url ? <audio controls preload="none" src={segment.audio_url} style={{ width: "100%", marginTop: 6 }} /> : null}
            {editing === segment.id ? (
              <>
                <label className="field" style={{ marginTop: 8 }}>
                  <textarea value={text} onChange={e => setText(e.target.value)} />
                </label>
                <button className="small go" onClick={() => save(segment)}>
                  Save script
                </button>{" "}
                <button className="small" onClick={() => setEditing(null)}>
                  Cancel
                </button>
              </>
            ) : (
              <>
                <p className="script">{segment.script || <span className="muted">(written at assembly time)</span>}</p>
                {segment.script ? (
                  <button
                    className="small"
                    onClick={() => {
                      setEditing(segment.id);
                      setText(segment.script);
                    }}
                  >
                    Edit script
                  </button>
                ) : null}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
