"use client";

import { FormEvent, useEffect, useState } from "react";
import { ReadinessPanel } from "../components/readiness-panel";
import { AnswerScope, AnswerStreamEvent, Citation, Course, fetchCourses, requestAnswerStream } from "../lib/api";

// Select values are serialized scopes, so subject and course names need no escaping.
const scopeValue = (scope: AnswerScope) => JSON.stringify(scope);
const ALL_COURSES = scopeValue({});
const COURSES_RETRY_MS = 5000;

// Only PDFs have pages; Markdown, text, and spreadsheet sources are cited by section.
const isPaginated = (filename: string) => filename.toLowerCase().endsWith(".pdf");

function groupBySubject(courses: Course[]): [string, Course[]][] {
  const groups = new Map<string, Course[]>();
  for (const course of courses) groups.set(course.subject, [...(groups.get(course.subject) ?? []), course]);
  return [...groups.entries()];
}

export default function Home() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [coursesError, setCoursesError] = useState(false);
  const [scope, setScope] = useState(ALL_COURSES);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    // Retry until the API answers, so a page opened while the stack is starting recovers.
    const load = () => {
      fetchCourses()
        .then((items) => { if (active) { setCourses(items); setCoursesError(false); } })
        .catch(() => {
          if (!active) return;
          setCoursesError(true);
          retry = setTimeout(load, COURSES_RETRY_MS);
        });
    };
    load();
    return () => { active = false; clearTimeout(retry); };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setAnswer(""); setCitations([]); setNotice(""); setStatus("Generating answer…");
    try {
      await requestAnswerStream(JSON.parse(scope) as AnswerScope, question, (item: AnswerStreamEvent) => {
        if (item.type === "delta") setAnswer((value) => value + item.text);
        if (item.type === "citations") setCitations(item.citations);
        if (item.type === "completed") setStatus("Answer complete");
        if (item.type === "clarification" || item.type === "abstention") { setNotice(item.message); setStatus(""); }
        if (item.type === "error") setStatus(item.message);
      });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Answer stream failed. Try again.");
    } finally { setBusy(false); }
  }

  const showCourse = !(JSON.parse(scope) as AnswerScope).courseId;

  return (
    <main className="page">
      <section className="card" aria-labelledby="title">
        <p className="eyebrow">Local development</p>
        <h1 id="title">Course assistant</h1>
        <p className="intro">Ask a question grounded in the ingested course material.</p>
        <form className="chat-form" onSubmit={submit}>
          <label htmlFor="course-id">Course</label>
          <select id="course-id" value={scope} onChange={(event) => setScope(event.target.value)} disabled={busy}>
            <option value={ALL_COURSES}>All courses</option>
            {groupBySubject(courses).map(([subject, items]) => (
              <optgroup key={subject} label={subject}>
                <option value={scopeValue({ subject })}>All of {subject}</option>
                {items.map((course) => <option key={course.course_id} value={scopeValue({ courseId: course.course_id })}>{course.course_id} ({course.document_count} {course.document_count === 1 ? "document" : "documents"})</option>)}
              </optgroup>
            ))}
          </select>
          {coursesError && <p className="field-hint">Could not load the course list; questions will search all courses.</p>}
          {!coursesError && courses.length === 0 && <p className="field-hint">No courses ingested yet.</p>}
          <label htmlFor="question">Question</label>
          <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} required disabled={busy} rows={3} />
          <button type="submit" disabled={busy || !question.trim()}>{busy ? "Answering…" : "Ask"}</button>
        </form>
        {status && <p className="chat-status" role="status" aria-live="polite">{status}</p>}
        {notice && <p className="chat-notice" role="status" aria-live="polite">{notice}</p>}
        {answer && <section aria-labelledby="answer-heading"><h2 id="answer-heading">Answer</h2><p className="answer-text">{answer}</p></section>}
        {citations.length > 0 && <section aria-labelledby="citations-heading"><h2 id="citations-heading">Sources</h2><ul className="citation-list">{citations.map((citation) => <li key={citation.citation_id}>{showCourse && <span className="citation-course">{citation.subject} › {citation.course_id} · </span>}<strong>{citation.document_title ?? citation.source_filename}</strong>{isPaginated(citation.source_filename) && <> · p. {citation.physical_page_number}</>}{citation.section_title && <span className="citation-section">{citation.section_title}</span>}<blockquote>{citation.excerpt}</blockquote></li>)}</ul></section>}
        <ReadinessPanel />
      </section>
    </main>
  );
}
