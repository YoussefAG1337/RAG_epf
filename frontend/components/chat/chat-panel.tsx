"use client";

import { Sparkles } from "lucide-react";
import { useEffect, useRef } from "react";
import type { Citation, Course, Scope } from "../../lib/api";
import type { ChatMessage } from "../../lib/chat";
import { Composer } from "./composer";
import { MessageView } from "./message-view";

type Props = {
  title: string;
  messages: ChatMessage[];
  courses: Course[];
  scope: Scope;
  scopeText: string;
  busy: boolean;
  activeCitation: { messageId: string; number: number } | null;
  onSend: (question: string) => void;
  onStop: () => void;
  onClearScope: () => void;
  onOpenCitation: (messageId: string, citation: Citation) => void;
};

function suggestions(courses: Course[], scope: Scope, scopeText: string): string[] {
  if (scope.document_id) {
    return ["Résume ce document en quelques points.", "Quelles sont les notions clés de ce document ?", "Quels exercices ou questions contient ce document ?"];
  }
  if (scope.course_id) {
    return [`Quels sont les thèmes principaux du cours ${scopeText} ?`, `Explique la notion la plus importante du cours ${scopeText} avec un exemple.`, `Quels exercices sont proposés dans le cours ${scopeText} ?`];
  }
  const names = courses.slice(0, 3).map((course) => course.course_id);
  return names.length
    ? names.map((name) => `Quelles sont les notions clés du cours ${name} ?`)
    : ["Importez des cours pour commencer à poser des questions."];
}

export function ChatPanel(props: Props) {
  const { messages, busy } = props;
  const end = useRef<HTMLDivElement>(null);
  const lastLength = messages.length;
  const lastClaims = messages.at(-1)?.role === "assistant" ? (messages.at(-1) as { claims: unknown[] }).claims.length : 0;

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lastLength, lastClaims]);

  return (
    <section className="flex h-full min-w-0 flex-1 flex-col bg-white" aria-label="Conversation">
      <header className="flex h-14 shrink-0 items-center border-b border-slate-200 px-6">
        <h1 className="truncate text-sm font-semibold text-slate-800">{props.title}</h1>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center px-6 text-center">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-50 text-brand-600">
              <Sparkles className="h-6 w-6" aria-hidden />
            </div>
            <h2 className="text-xl font-semibold text-slate-900">Que voulez-vous réviser ?</h2>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">
              Les réponses s’appuient uniquement sur vos supports de cours. Cliquez sur un numéro de source pour ouvrir le document au passage exact.
            </p>
            <div className="mt-6 grid w-full gap-2">
              {suggestions(props.courses, props.scope, props.scopeText).map((suggestion) => (
                <button key={suggestion} type="button" onClick={() => props.onSend(suggestion)} disabled={busy || props.courses.length === 0} className="rounded-xl border border-slate-200 px-4 py-2.5 text-left text-sm text-slate-700 transition-colors hover:border-brand-500/50 hover:bg-brand-50/50 disabled:opacity-60">
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-6 px-6 py-6">
            {messages.map((message) => (
              <MessageView key={message.id} message={message} activeCitation={props.activeCitation} onOpenCitation={props.onOpenCitation} />
            ))}
            <div ref={end} />
          </div>
        )}
      </div>

      <div className="shrink-0 px-6 pb-4 pt-2">
        <div className="mx-auto max-w-3xl">
          <Composer scope={props.scope} scopeText={props.scopeText} busy={busy} onSend={props.onSend} onStop={props.onStop} onClearScope={props.onClearScope} />
          <p className="mt-2 text-center text-[11px] text-slate-400">Vérifiez toujours les passages cités dans les documents originaux.</p>
        </div>
      </div>
    </section>
  );
}
