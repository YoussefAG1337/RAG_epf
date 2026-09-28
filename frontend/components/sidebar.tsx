"use client";

import { BookMarked, ChevronDown, ChevronRight, FileSpreadsheet, FileText, GraduationCap, Library, MessageSquare, Pencil, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { clsx } from "clsx";
import type { ConversationSummary, Course, DocumentSummary, Scope } from "../lib/api";
import { ReadinessPanel } from "./readiness-panel";

type Props = {
  courses: Course[];
  documents: DocumentSummary[];
  libraryError: boolean;
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  scope: Scope;
  onNewConversation: () => void;
  onOpenConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string) => void;
  onDeleteConversation: (id: string) => void;
  onSelectScope: (scope: Scope) => void;
  onOpenDocument: (document: DocumentSummary) => void;
};

function dayLabel(iso: string): string {
  const date = new Date(iso);
  const today = new Date();
  const days = Math.floor((new Date(today.toDateString()).getTime() - new Date(date.toDateString()).getTime()) / 86_400_000);
  if (days <= 0) return "Aujourd'hui";
  if (days === 1) return "Hier";
  if (days < 7) return "7 derniers jours";
  return "Plus ancien";
}

function ConversationItem({ conversation, active, onOpen, onRename, onDelete }: { conversation: ConversationSummary; active: boolean; onOpen: () => void; onRename: (title: string) => void; onDelete: () => void }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(conversation.title);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (title.trim()) onRename(title.trim());
    setEditing(false);
  };
  if (editing) {
    return (
      <form onSubmit={submit} className="px-1">
        <input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} onBlur={submit} aria-label="Titre de la conversation" className="w-full rounded-md border border-brand-500 bg-white px-2 py-1 text-sm focus:outline-none" />
      </form>
    );
  }
  return (
    <div className={clsx("group flex items-center rounded-lg", active ? "bg-brand-50 text-brand-700" : "text-slate-700 hover:bg-slate-100")}>
      <button type="button" onClick={onOpen} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left text-sm" aria-current={active ? "page" : undefined}>
        <MessageSquare className="h-3.5 w-3.5 shrink-0 opacity-60" aria-hidden />
        <span className="truncate">{conversation.title}</span>
      </button>
      <div className="mr-1 hidden shrink-0 items-center group-focus-within:flex group-hover:flex">
        <button type="button" onClick={() => setEditing(true)} className="rounded p-1 text-slate-400 hover:bg-white hover:text-slate-700" aria-label={`Renommer « ${conversation.title} »`}>
          <Pencil className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          onClick={() => {
            if (window.confirm(`Supprimer la conversation « ${conversation.title} » ?`)) onDelete();
          }}
          className="rounded p-1 text-slate-400 hover:bg-white hover:text-red-600"
          aria-label={`Supprimer « ${conversation.title} »`}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
}

export function Sidebar(props: Props) {
  const { courses, documents, libraryError, conversations, activeConversationId, scope } = props;
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const subjects = [...new Set(courses.map((course) => course.subject))];
  const showSubjects = subjects.length > 1 || (subjects.length === 1 && subjects[0] !== "Général");

  const groups = conversations.reduce<Record<string, ConversationSummary[]>>((result, conversation) => {
    (result[dayLabel(conversation.updated_at)] ??= []).push(conversation);
    return result;
  }, {});

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-slate-200 bg-slate-50/80">
      <div className="flex items-center gap-2.5 px-4 pb-3 pt-4">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 text-white shadow-sm">
          <GraduationCap className="h-5 w-5" aria-hidden />
        </div>
        <div>
          <p className="text-sm font-bold leading-tight text-slate-900">Assistant de cours</p>
          <p className="text-xs text-slate-500">Réponses sourcées</p>
        </div>
      </div>
      <div className="px-3">
        <button type="button" onClick={props.onNewConversation} className="flex w-full items-center justify-center gap-2 rounded-xl bg-brand-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-brand-700">
          <Plus className="h-4 w-4" aria-hidden /> Nouvelle conversation
        </button>
      </div>

      <nav className="mt-4 min-h-0 flex-1 space-y-5 overflow-y-auto px-3 pb-4" aria-label="Navigation">
        <section>
          <h2 className="mb-1.5 flex items-center gap-1.5 px-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            <Library className="h-3.5 w-3.5" aria-hidden /> Bibliothèque
          </h2>
          <button type="button" onClick={() => props.onSelectScope({})} className={clsx("mb-0.5 w-full rounded-lg px-2 py-1.5 text-left text-sm", !scope.subject && !scope.course_id && !scope.document_id ? "bg-brand-50 font-medium text-brand-700" : "text-slate-700 hover:bg-slate-100")}>
            Tous les cours
          </button>
          {libraryError && <p className="px-2 py-1 text-xs text-slate-500">Bibliothèque indisponible, nouvelle tentative…</p>}
          {!libraryError && courses.length === 0 && <p className="px-2 py-1 text-xs text-slate-500">Aucun cours importé pour l’instant.</p>}
          {subjects.map((subject) => (
            <div key={subject}>
              {showSubjects && (
                <button type="button" onClick={() => props.onSelectScope({ subject })} className={clsx("mt-1 w-full rounded-lg px-2 py-1 text-left text-xs font-semibold", scope.subject === subject && !scope.course_id ? "bg-brand-50 text-brand-700" : "text-slate-500 hover:bg-slate-100")}>
                  {subject}
                </button>
              )}
              {courses
                .filter((course) => course.subject === subject)
                .map((course) => {
                  const open = expanded[course.course_id] ?? scope.course_id === course.course_id;
                  const courseDocuments = documents.filter((document) => document.course_id === course.course_id);
                  const selected = scope.course_id === course.course_id && !scope.document_id;
                  return (
                    <div key={course.course_id} className={showSubjects ? "ml-2" : undefined}>
                      <div className={clsx("flex items-center rounded-lg", selected ? "bg-brand-50 text-brand-700" : "text-slate-700 hover:bg-slate-100")}>
                        <button type="button" onClick={() => setExpanded((state) => ({ ...state, [course.course_id]: !open }))} className="rounded p-1 text-slate-400" aria-label={open ? `Replier ${course.course_id}` : `Déplier ${course.course_id}`} aria-expanded={open}>
                          {open ? <ChevronDown className="h-3.5 w-3.5" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden />}
                        </button>
                        <button type="button" onClick={() => props.onSelectScope({ course_id: course.course_id })} className={clsx("flex min-w-0 flex-1 items-center gap-1.5 py-1.5 pr-2 text-left text-sm", selected && "font-medium")}>
                          <BookMarked className="h-3.5 w-3.5 shrink-0 opacity-60" aria-hidden />
                          <span className="truncate">{course.course_id}</span>
                          <span className="ml-auto text-xs text-slate-400">{course.document_count}</span>
                        </button>
                      </div>
                      {open && (
                        <ul className="mb-1 ml-5 border-l border-slate-200 pl-2">
                          {courseDocuments.map((document) => {
                            const Icon = document.source_filename.toLowerCase().endsWith(".xlsx") ? FileSpreadsheet : FileText;
                            return (
                              <li key={document.id}>
                                <button type="button" onClick={() => props.onOpenDocument(document)} title={document.source_filename} className={clsx("flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[13px]", scope.document_id === document.id ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-100")}>
                                  <Icon className="h-3.5 w-3.5 shrink-0 opacity-60" aria-hidden />
                                  <span className="truncate">{document.title}</span>
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  );
                })}
            </div>
          ))}
        </section>

        <section>
          <h2 className="mb-1.5 flex items-center gap-1.5 px-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            <MessageSquare className="h-3.5 w-3.5" aria-hidden /> Conversations
          </h2>
          {conversations.length === 0 && <p className="px-2 py-1 text-xs text-slate-500">Vos conversations apparaîtront ici.</p>}
          {Object.entries(groups).map(([label, items]) => (
            <div key={label} className="mb-2">
              <p className="px-2 pb-0.5 pt-1 text-[11px] text-slate-400">{label}</p>
              {items.map((conversation) => (
                <ConversationItem
                  key={conversation.id}
                  conversation={conversation}
                  active={conversation.id === activeConversationId}
                  onOpen={() => props.onOpenConversation(conversation.id)}
                  onRename={(title) => props.onRenameConversation(conversation.id, title)}
                  onDelete={() => props.onDeleteConversation(conversation.id)}
                />
              ))}
            </div>
          ))}
        </section>
      </nav>
      <div className="border-t border-slate-200 px-4 py-2.5">
        <ReadinessPanel />
      </div>
    </aside>
  );
}
