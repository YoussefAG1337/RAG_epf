"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ReadinessPanel } from "../components/readiness-panel";
import {
  AnswerStreamEvent,
  Citation,
  ConversationTurn,
  Course,
  fetchCourses,
  requestAnswerStream,
  uploadCoursePdf,
} from "../lib/api";

type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  state: "streaming" | "complete" | "error";
};

export default function Home() {
  const nextMessageId = useRef(0);
  const [courseId, setCourseId] = useState("");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationHistory, setConversationHistory] = useState<ConversationTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const [courses, setCourses] = useState<Course[]>([]);
  const [coursesLoading, setCoursesLoading] = useState(true);
  const [coursesError, setCoursesError] = useState("");
  const [newCourseName, setNewCourseName] = useState("");
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState("");

  const loadCourses = useCallback(async () => {
    setCoursesLoading(true);
    setCoursesError("");
    try {
      const availableCourses = await fetchCourses();
      setCourses(availableCourses);
      setCourseId((selected) => availableCourses.some((course) => course.course_id === selected) ? selected : "");
    } catch (error) {
      setCoursesError(error instanceof Error ? error.message : "Could not load courses.");
    } finally {
      setCoursesLoading(false);
    }
  }, []);

  useEffect(() => { void loadCourses(); }, [loadCourses]);

  function startNewChat() {
    setMessages([]);
    setConversationHistory([]);
    setQuestion("");
  }

  async function addCourse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (uploading || !pdfFile) return;
    const form = event.currentTarget;
    setUploading(true);
    setUploadStatus("Uploading and preparing course PDF...");
    try {
      const created = await uploadCoursePdf(newCourseName, pdfFile);
      await loadCourses();
      setCourseId(created.course_id);
      startNewChat();
      setNewCourseName("");
      setPdfFile(null);
      form.reset();
      setUploadStatus(`Course added. ${created.page_count} pages and ${created.chunk_count} searchable passages are ready.`);
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : "Course upload failed. Try again.");
    } finally {
      setUploading(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !courseId || !question.trim()) return;

    const currentQuestion = question.trim();
    const userId = ++nextMessageId.current;
    const assistantId = ++nextMessageId.current;
    setMessages((current) => [
      ...current,
      { id: userId, role: "user", content: currentQuestion, citations: [], state: "complete" },
      { id: assistantId, role: "assistant", content: "", citations: [], state: "streaming" },
    ]);
    setQuestion("");
    setBusy(true);

    let answerText = "";
    const streamOutcome: { state: "complete" | "error"; historyMessage: string | null } = {
      state: "error",
      historyMessage: null,
    };
    const updateAssistant = (update: Partial<ChatMessage>) => {
      setMessages((current) => current.map((message) =>
        message.id === assistantId ? { ...message, ...update } : message,
      ));
    };

    try {
      await requestAnswerStream(
        courseId,
        currentQuestion,
        (item: AnswerStreamEvent) => {
          if (item.type === "delta") {
            answerText += item.text;
            updateAssistant({ content: answerText });
          } else if (item.type === "citations") {
            updateAssistant({ citations: item.citations });
          } else if (item.type === "completed") {
            streamOutcome.state = "complete";
            streamOutcome.historyMessage = answerText;
            updateAssistant({ state: "complete" });
          } else if (item.type === "clarification" || item.type === "abstention") {
            streamOutcome.state = "complete";
            streamOutcome.historyMessage = item.message;
            updateAssistant({ content: item.message, state: "complete" });
          } else if (item.type === "error") {
            streamOutcome.state = "error";
            updateAssistant({
              content: answerText ? `${answerText}\n\n${item.message}` : item.message,
              state: "error",
            });
          }
        },
        undefined,
        conversationHistory,
      );
      if (streamOutcome.state === "complete" && streamOutcome.historyMessage !== null) {
        setConversationHistory((history) => {
          const nextHistory: ConversationTurn[] = [
            ...history,
            { role: "user", content: currentQuestion.slice(0, 2000) },
            { role: "assistant", content: streamOutcome.historyMessage.slice(0, 2000) },
          ];
          return nextHistory.slice(-12);
        });
      }
    } catch (error) {
      updateAssistant({
        content: [answerText, error instanceof Error ? error.message : "The response failed. Try again."]
          .filter(Boolean)
          .join("\n\n"),
        state: "error",
      });
    } finally {
      setBusy(false);
    }
  }

  function selectCourse(nextCourseId: string) {
    if (nextCourseId !== courseId) startNewChat();
    setCourseId(nextCourseId);
  }

  return (
    <main className="page">
      <section className="card" aria-labelledby="title">
        <p className="eyebrow">Local development</p>
        <h1 id="title">Course assistant</h1>
        <p className="intro">Ask questions and follow up about a course&apos;s ingested material.</p>
        <form className="chat-form" onSubmit={addCourse}>
          <h2>Add a course</h2>
          <p>Each searchable PDF creates one course. Course names must be unique. PDFs can be up to 25 MB.</p>
          <label htmlFor="new-course-name">Course name</label>
          <input id="new-course-name" value={newCourseName} onChange={(event) => setNewCourseName(event.target.value)} required maxLength={200} disabled={uploading} />
          <label htmlFor="course-pdf">PDF file</label>
          <input id="course-pdf" type="file" accept="application/pdf,.pdf" required disabled={uploading} onChange={(event) => setPdfFile(event.target.files?.[0] ?? null)} />
          <button type="submit" disabled={uploading || !newCourseName.trim() || !pdfFile}>{uploading ? "Preparing..." : "Add course"}</button>
          {uploadStatus && <p role="status" aria-live="polite">{uploadStatus}</p>}
        </form>
        <div className="chat-form chat-controls">
          <label htmlFor="course-id">Course</label>
          <select id="course-id" value={courseId} onChange={(event) => selectCourse(event.target.value)} required disabled={busy || uploading || coursesLoading || courses.length === 0}>
            <option value="">{coursesLoading ? "Loading courses..." : "Choose a course"}</option>
            {courses.map((course) => (
              <option key={course.course_id} value={course.course_id}>
                {course.course_id} - {course.source_filenames.join(", ")}
              </option>
            ))}
          </select>
          {coursesError && <p role="alert">Could not load courses. {coursesError} <button type="button" onClick={() => void loadCourses()} disabled={coursesLoading}>Try again</button></p>}
          {!coursesLoading && !coursesError && courses.length === 0 && <p role="status">No courses are available yet. Add a course above to get started.</p>}
          {messages.length > 0 && <button type="button" onClick={startNewChat} disabled={busy}>New chat</button>}
        </div>
        <section className="chat-transcript" aria-label="Conversation" aria-live="polite">
          {messages.map((message) => (
            <article className={`chat-message chat-message-${message.role} chat-message-${message.state}`} key={message.id}>
              <h2>{message.role === "user" ? "You" : "Assistant"}</h2>
              <p className="message-text">{message.content || (message.state === "streaming" ? "Thinking..." : "")}</p>
              {message.citations.length > 0 && <section aria-label="Sources"><ul className="citation-list">{message.citations.map((citation) => <li key={citation.citation_id}><strong>{citation.source_filename}</strong> - page {citation.physical_page_number}<blockquote>{citation.excerpt}</blockquote></li>)}</ul></section>}
            </article>
          ))}
        </section>
        <form className="chat-form" onSubmit={submit}>
          <label htmlFor="question">Message</label>
          <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} required disabled={busy || !courseId} rows={3} placeholder="Ask a question or follow up..." />
          <button type="submit" disabled={busy || uploading || coursesLoading || !courseId || !question.trim()}>{busy ? "Responding..." : "Send"}</button>
        </form>
        <ReadinessPanel />
      </section>
    </main>
  );
}
