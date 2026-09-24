"use client";

import { FormEvent, useState } from "react";
import { ReadinessPanel } from "../components/readiness-panel";
import { AnswerStreamEvent, Citation, requestAnswerStream } from "../lib/api";

export default function Home() {
  const [courseId, setCourseId] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setAnswer(""); setCitations([]); setStatus("Generating answer…");
    try {
      await requestAnswerStream(courseId, question, (item: AnswerStreamEvent) => {
        if (item.type === "delta") setAnswer((value) => value + item.text);
        if (item.type === "citations") setCitations(item.citations);
        if (item.type === "completed") setStatus("Answer complete");
        if (item.type === "clarification" || item.type === "abstention") setStatus(item.message);
        if (item.type === "error") setStatus(item.message);
      });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Answer stream failed. Try again.");
    } finally { setBusy(false); }
  }

  return (
    <main className="page">
      <section className="card" aria-labelledby="title">
        <p className="eyebrow">Local development</p>
        <h1 id="title">Course assistant</h1>
        <p className="intro">Ask a question grounded in a course’s ingested material.</p>
        <form className="chat-form" onSubmit={submit}>
          <label htmlFor="course-id">Course ID</label>
          <input id="course-id" value={courseId} onChange={(event) => setCourseId(event.target.value)} required disabled={busy} />
          <label htmlFor="question">Question</label>
          <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} required disabled={busy} rows={3} />
          <button type="submit" disabled={busy || !courseId.trim() || !question.trim()}>{busy ? "Answering…" : "Ask"}</button>
        </form>
        {status && <p className="chat-status" role="status" aria-live="polite">{status}</p>}
        {answer && <section aria-labelledby="answer-heading"><h2 id="answer-heading">Answer</h2><p className="answer-text">{answer}</p></section>}
        {citations.length > 0 && <section aria-labelledby="citations-heading"><h2 id="citations-heading">Sources</h2><ul className="citation-list">{citations.map((citation) => <li key={citation.citation_id}><strong>{citation.source_filename}</strong> · page {citation.physical_page_number}<blockquote>{citation.excerpt}</blockquote></li>)}</ul></section>}
        <ReadinessPanel />
      </section>
    </main>
  );
}
