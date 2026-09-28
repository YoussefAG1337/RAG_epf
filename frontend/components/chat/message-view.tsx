"use client";

import { AlertTriangle, BookOpen, FileSearch, FileText, Loader2, SearchX } from "lucide-react";
import { clsx } from "clsx";
import type { Citation } from "../../lib/api";
import { type ChatMessage, citationLabel, isPaginated } from "../../lib/chat";
import { InlineText } from "./inline-text";


type Props = {
  message: ChatMessage;
  activeCitation: { messageId: string; number: number } | null;
  onOpenCitation: (messageId: string, citation: Citation) => void;
};

function CitationMarker({ citation, active, onOpen }: { citation: Citation; active: boolean; onOpen: () => void }) {
  const quote = citation.quotes[0];
  return (
    <button
      type="button"
      onClick={onOpen}
      className={clsx(
        "group relative mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] -translate-y-1.5 items-center justify-center rounded-md px-1 align-baseline text-[0.7rem] font-semibold transition-colors",
        active ? "bg-amber-400 text-amber-950" : "bg-brand-100 text-brand-700 hover:bg-brand-500 hover:text-white",
      )}
      aria-label={`Source ${citation.number} : ${citationLabel(citation)}`}
    >
      {citation.number}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-2 hidden w-72 -translate-x-1/2 rounded-lg bg-slate-900 p-3 text-left text-xs font-normal leading-relaxed text-white shadow-xl group-hover:block group-focus-visible:block"
      >
        <span className="block font-semibold text-amber-200">{citationLabel(citation)}</span>
        <span className="mt-0.5 block text-slate-300">
          {citation.course_id}
          {isPaginated(citation.source_filename) ? ` · p. ${citation.highlights[0]?.page ?? citation.page}` : ""}
        </span>
        {quote && (
          <span className="mt-1.5 block italic text-slate-100">
            « <InlineText text={quote} /> »
          </span>
        )}
      </span>
    </button>
  );
}

function SourceCard({ citation, active, onOpen }: { citation: Citation; active: boolean; onOpen: () => void }) {
  const page = citation.highlights[0]?.page ?? citation.page;
  return (
    <button
      type="button"
      onClick={onOpen}
      className={clsx(
        "flex w-full items-start gap-3 rounded-xl border p-3 text-left transition-all",
        active ? "border-amber-400 bg-amber-50 shadow-sm" : "border-slate-200 bg-white hover:border-brand-500/50 hover:shadow-sm",
      )}
    >
      <span className={clsx("flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-bold", active ? "bg-amber-400 text-amber-950" : "bg-brand-50 text-brand-700")}>
        {citation.number}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-800">
          <FileText className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
          <span className="truncate">{citationLabel(citation)}</span>
        </span>
        <span className="mt-0.5 block truncate text-xs text-slate-500">
          {citation.course_id}
          {isPaginated(citation.source_filename) ? ` · page ${page}` : ""}
          {citation.section_title ? ` · ${citation.section_title}` : ""}
        </span>
        {citation.quotes[0] && (
          <span className="mt-1.5 line-clamp-2 block text-xs italic text-slate-600">
            « <InlineText text={citation.quotes[0]} /> »
          </span>
        )}
      </span>
    </button>
  );
}

export function MessageView({ message, activeCitation, onOpenCitation }: Props) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-[15px] leading-relaxed text-white shadow-sm">
          {message.content}
        </p>
      </div>
    );
  }

  const byNumber = new Map(message.citations.map((citation) => [citation.number, citation]));
  const isActive = (number: number) => activeCitation?.messageId === message.id && activeCitation.number === number;
  const streaming = message.status === "streaming";

  if (message.status === "abstained" || message.status === "error") {
    const abstained = message.status === "abstained";
    return (
      <div className={clsx("flex max-w-[92%] items-start gap-3 rounded-2xl border px-4 py-3 text-[15px] leading-relaxed", abstained ? "border-amber-200 bg-amber-50 text-amber-900" : "border-red-200 bg-red-50 text-red-800")} role="status">
        {abstained ? <SearchX className="mt-0.5 h-5 w-5 shrink-0" aria-hidden /> : <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />}
        <p>{message.message}</p>
      </div>
    );
  }

  return (
    <div className="max-w-[92%] space-y-4" aria-busy={streaming}>
      {streaming && message.claims.length === 0 && (
        <p className="flex items-center gap-2 text-sm text-slate-500" role="status">
          <Loader2 className="h-4 w-4 animate-spin text-brand-500" aria-hidden />
          {message.stage === "writing" ? "Rédaction de la réponse à partir des extraits…" : "Recherche dans les supports de cours…"}
        </p>
      )}
      {message.claims.length > 0 && (
        <p className={clsx("text-[15px] leading-7 text-slate-800", streaming && "streaming-caret")}>
          {message.claims.map((claim, index) => (
            <span key={index}>
              <InlineText text={claim.text} />
              {claim.citations.map((number) => {
                const citation = byNumber.get(number);
                return citation ? (
                  <CitationMarker key={number} citation={citation} active={isActive(number)} onOpen={() => onOpenCitation(message.id, citation)} />
                ) : (
                  <span key={number} className="mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] -translate-y-1.5 items-center justify-center rounded-md bg-slate-100 px-1 text-[0.7rem] font-semibold text-slate-400">
                    {number}
                  </span>
                );
              })}{" "}
            </span>
          ))}
        </p>
      )}
      {message.retrievalQuery && !streaming && (
        <p className="flex items-center gap-1.5 text-xs text-slate-400">
          <FileSearch className="h-3.5 w-3.5" aria-hidden /> Recherche effectuée : « {message.retrievalQuery} »
        </p>
      )}
      {message.citations.length > 0 && (
        <section aria-label="Sources">
          <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <BookOpen className="h-3.5 w-3.5" aria-hidden /> Sources ({message.citations.length})
          </h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {message.citations.map((citation) => (
              <SourceCard key={citation.number} citation={citation} active={isActive(citation.number)} onOpen={() => onOpenCitation(message.id, citation)} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
