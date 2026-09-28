"use client";

import { ArrowUp, BookMarked, FileText, Library, Square, X } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { clsx } from "clsx";
import type { Scope } from "../../lib/api";

type Props = {
  scope: Scope;
  scopeText: string;
  busy: boolean;
  onSend: (question: string) => void;
  onStop: () => void;
  onClearScope: () => void;
};

export function Composer({ scope, scopeText, busy, onSend, onStop, onClearScope }: Props) {
  const [value, setValue] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [value]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const question = value.trim();
    if (!question || busy) return;
    onSend(question);
    setValue("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  const scoped = Boolean(scope.document_id || scope.course_id || scope.subject);
  const ScopeIcon = scope.document_id ? FileText : scope.course_id ? BookMarked : Library;

  return (
    <form onSubmit={submit} className="rounded-2xl border border-slate-200 bg-white p-2 shadow-sm focus-within:border-brand-500/60 focus-within:ring-4 focus-within:ring-brand-500/10">
      <div className="flex items-center gap-2 px-2 pt-1">
        <span className={clsx("inline-flex max-w-full items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium", scoped ? "bg-brand-50 text-brand-700" : "bg-slate-100 text-slate-600")}>
          <ScopeIcon className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate">{scope.document_id ? `Document : ${scopeText}` : scopeText}</span>
          {scoped && (
            <button type="button" onClick={onClearScope} className="-mr-1 rounded p-0.5 hover:bg-brand-100" aria-label="Chercher dans tous les cours">
              <X className="h-3 w-3" aria-hidden />
            </button>
          )}
        </span>
      </div>
      <div className="flex items-end gap-2">
        <label htmlFor="question" className="sr-only">
          Question
        </label>
        <textarea
          id="question"
          ref={textarea}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          maxLength={2000}
          placeholder={scope.document_id ? "Posez une question sur ce document…" : "Posez une question sur vos cours…"}
          className="max-h-[200px] min-h-[44px] flex-1 resize-none bg-transparent px-2 py-2.5 text-[15px] leading-relaxed text-slate-900 placeholder:text-slate-400 focus:outline-none"
        />
        {busy ? (
          <button type="button" onClick={onStop} className="mb-1 flex h-9 w-9 items-center justify-center rounded-xl bg-slate-800 text-white hover:bg-slate-700" aria-label="Arrêter">
            <Square className="h-3.5 w-3.5 fill-current" aria-hidden />
          </button>
        ) : (
          <button type="submit" disabled={!value.trim()} className="mb-1 flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:bg-slate-200 disabled:text-slate-400" aria-label="Envoyer">
            <ArrowUp className="h-4 w-4" aria-hidden />
          </button>
        )}
      </div>
    </form>
  );
}
