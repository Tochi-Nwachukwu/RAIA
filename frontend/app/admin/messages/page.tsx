"use client";

/** Listeners' WhatsApp messages: what came in, how the desk answered, what is queued for air. */

import Link from "next/link";
import { useAdmin } from "@/lib/admin";
import { ErrorNote, Loading } from "@/components/Chip";

interface Thread {
  id: string;
  first_name: string | null;
  city: string | null;
  consent_on_air: boolean;
  updated_at: string;
  messages: { at?: string; text?: string; category?: string; reply?: string }[];
  on_air: { id: string; question: string; answer: string | null; status: string; first_name: string; city: string }[];
}

export default function MessagesPage() {
  const { data, error } = useAdmin<{ threads: Thread[] }>("/messages", 15000);

  return (
    <main>
      <div className="toolbar">
        <div className="tabs">
          <Link href="/admin">Content</Link>
          <span className="on" style={{ padding: "7px 16px", borderRadius: 999, border: "1px solid var(--line)" }}>
            Messages
          </span>
        </div>
      </div>

      <h1>What listeners sent</h1>
      <p className="lede">
        Phone numbers are never stored: each thread is a salted hash, and the whole thread expires a few days after the
        last message. A question only goes on air with its sender&apos;s consent, and only ever as a first name and a
        city.
      </p>

      <ErrorNote error={error} />
      {!data && !error ? <Loading what="messages" /> : null}

      {data && !data.threads.length ? (
        <section className="panel">
          <p className="muted">
            No messages yet. Connect the WhatsApp Cloud API (see <code>backend/README.md</code>) and they land here;
            without it, the desk writes its replies to <code>runs/outbox.jsonl</code>.
          </p>
        </section>
      ) : null}

      {data?.threads.map(thread => (
        <section className="panel" key={thread.id} style={{ marginBottom: 14 }}>
          <div className="toolbar" style={{ margin: 0 }}>
            <strong>
              {thread.first_name || "Someone"}
              {thread.city ? ` in ${thread.city}` : ""}
            </strong>
            <span className={`chip ${thread.consent_on_air ? "good" : ""}`}>
              {thread.consent_on_air ? "consented to air" : "no consent"}
            </span>
            <div className="spacer" />
            <span className="src">{new Date(thread.updated_at).toLocaleString()}</span>
          </div>
          {thread.messages.map((message, i) => (
            <div className="segment" key={i}>
              <div className="meta">
                {message.category || "message"}
                {message.at ? ` · ${new Date(message.at).toLocaleString()}` : ""}
              </div>
              <p className="script">{message.text}</p>
              {message.reply ? <p className="src">Replied: {message.reply}</p> : null}
            </div>
          ))}
          {thread.on_air.map(question => (
            <div className="segment" key={question.id}>
              <div className="meta">
                on air · <span className="chip warn">{question.status}</span>
              </div>
              <p className="script">
                {question.first_name} in {question.city} asks: {question.question}
              </p>
              {question.answer ? <p className="src">Answer: {question.answer}</p> : null}
            </div>
          ))}
        </section>
      ))}
    </main>
  );
}
