"use client";

/** Click an empty stretch of the day and this asks what goes there. */

import { useState } from "react";
import { adminApi, clockOf, type Options } from "@/lib/admin";
import { LANGUAGES } from "@/lib/api";

interface Props {
  date: string;
  startMinutes: number;
  options: Options | null;
  onClose: () => void;
  onCreated: (entryId: string) => void;
}

export function CreateDialog({ date, startMinutes, options, onClose, onCreated }: Props) {
  const [title, setTitle] = useState("");
  const [start, setStart] = useState(clockOf(startMinutes));
  const [duration, setDuration] = useState(20);
  const [languages, setLanguages] = useState<string[]>(["en"]);
  const [template, setTemplate] = useState("local_bulletin");
  const [presenter, setPresenter] = useState("idera");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const created = await adminApi<{ id: string }>(`/days/${date}/entries`, {
        method: "POST",
        body: JSON.stringify({
          title: title.trim() || "New bulletin",
          start: `${start}:00`,
          duration_min: duration,
          languages,
          template,
          presenter_primary: presenter,
        }),
      });
      onCreated(created.id);
    } catch (err) {
      setError((err as Error).message);
      setSaving(false);
    }
  };

  const presenters = Object.entries(options?.presenters || {});

  return (
    <div className="sheet" onClick={onClose}>
      <div className="sheet-card" onClick={e => e.stopPropagation()}>
        <div className="toolbar" style={{ marginTop: 0 }}>
          <h2 style={{ margin: 0 }}>New bulletin</h2>
          <div className="spacer" />
          <button className="small" onClick={onClose}>
            ✕
          </button>
        </div>

        <label className="field">
          <span>Title</span>
          <input autoFocus value={title} onChange={e => setTitle(e.target.value)} placeholder="Add title" />
        </label>

        <div style={{ display: "flex", gap: 12 }}>
          <label className="field" style={{ flex: 1 }}>
            <span>Starts</span>
            <input type="time" value={start} step={300} onChange={e => setStart(e.target.value)} />
          </label>
          <label className="field" style={{ flex: 1 }}>
            <span>Minutes on air</span>
            <input type="number" min={5} max={180} step={5} value={duration} onChange={e => setDuration(Number(e.target.value))} />
          </label>
        </div>

        <div className="field">
          <span>Languages</span>
          <div className="pickers">
            {(options ? options.languages : Object.keys(LANGUAGES)).map(code => (
              <button
                key={code}
                className={`picker${languages.includes(code) ? " on" : ""}`}
                onClick={() => setLanguages(languages.includes(code) ? languages.filter(l => l !== code) : [...languages, code])}
              >
                {LANGUAGES[code] || code}
              </button>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", gap: 12 }}>
          <label className="field" style={{ flex: 1 }}>
            <span>Shape</span>
            <select value={template} onChange={e => setTemplate(e.target.value)}>
              {Object.keys(options?.templates || { local_bulletin: [] }).map(name => (
                <option key={name} value={name}>
                  {name.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="field" style={{ flex: 1 }}>
            <span>Presenter</span>
            <select value={presenter} onChange={e => setPresenter(e.target.value)}>
              {presenters.map(([key, person]) => (
                <option key={key} value={key}>
                  {person.name} ({person.languages.join(", ")})
                </option>
              ))}
            </select>
          </label>
        </div>

        <p className="src">
          {options?.templates[template]?.length
            ? `${options.templates[template].length} segments: ${options.templates[template].join(", ")}`
            : ""}
        </p>
        {error ? <p className="error">{error}</p> : null}
        <div className="toolbar">
          <button className="small go" onClick={save} disabled={saving || !languages.length}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="small" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
