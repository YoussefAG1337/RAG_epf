"use client";

import dynamic from "next/dynamic";
import { ExternalLink, FileText, Loader2, MessageSquareText, X } from "lucide-react";
import { useEffect, useState } from "react";
import { type DocumentContent, type DocumentInfo, documentFileUrl, fetchDocument, fetchDocumentContent } from "../../lib/api";
import type { ViewerTarget } from "../../lib/chat";
import { SheetView, TextView } from "./text-view";

const PdfView = dynamic(() => import("./pdf-view"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Chargement du lecteur PDF…
    </div>
  ),
});

type Props = {
  target: ViewerTarget;
  scopedToDocument: boolean;
  onPageChange: (page: number) => void;
  onAskAboutDocument: (document: DocumentInfo) => void;
  onClose: () => void;
};

type Loaded = { document: DocumentInfo; content: DocumentContent | null };

export function DocumentViewer({ target, scopedToDocument, onPageChange, onAskAboutDocument, onClose }: Props) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    setLoaded((previous) => (previous?.document.id === target.documentId ? previous : null));
    (async () => {
      try {
        const document = await fetchDocument(target.documentId);
        const content = document.kind === "pdf" ? null : await fetchDocumentContent(target.documentId);
        if (active) setLoaded({ document, content });
      } catch {
        if (active) setError("Ce document n'est plus disponible. Relancez l'import des cours.");
      }
    })();
    return () => {
      active = false;
    };
  }, [target.documentId]);

  const document = loaded?.document;

  return (
    <section className="flex h-full w-full min-w-0 flex-1 flex-col bg-white" aria-label="Document">
      <header className="flex shrink-0 items-start gap-3 border-b border-slate-200 px-4 py-3">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <FileText className="h-4.5 w-4.5" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold text-slate-900" title={document?.title}>
            {document?.title ?? "Document"}
          </h2>
          <p className="flex min-w-0 items-center gap-1.5 text-xs text-slate-500">
            <span className="truncate">{document ? `${document.course_id} · ${document.source_filename.split("/").pop()}` : "Chargement…"}</span>
            {target.citationNumber !== null && <span className="shrink-0 rounded bg-amber-200 px-1.5 py-0.5 font-semibold text-amber-900">Source {target.citationNumber}</span>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {document && !scopedToDocument && (
            <button type="button" onClick={() => onAskAboutDocument(document)} className="hidden items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:border-brand-500/50 hover:text-brand-700 sm:flex">
              <MessageSquareText className="h-3.5 w-3.5" aria-hidden /> Poser une question sur ce document
            </button>
          )}
          {document && (
            <a href={documentFileUrl(document.id)} target="_blank" rel="noreferrer" className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-800" aria-label="Ouvrir le fichier original">
              <ExternalLink className="h-4 w-4" aria-hidden />
            </a>
          )}
          <button type="button" onClick={onClose} className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-800" aria-label="Fermer le document">
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
      </header>
      <div className="min-h-0 flex-1">
        {error ? (
          <p className="p-6 text-sm text-red-700">{error}</p>
        ) : !document ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Chargement…
          </div>
        ) : document.kind === "pdf" ? (
          <PdfView fileUrl={documentFileUrl(document.id)} page={target.page} highlights={target.highlights} nonce={target.nonce} onPageChange={onPageChange} />
        ) : document.kind === "spreadsheet" && loaded?.content ? (
          <SheetView content={loaded.content} highlights={target.highlights} nonce={target.nonce} />
        ) : loaded?.content ? (
          <TextView content={loaded.content} highlights={target.highlights} nonce={target.nonce} markdown={document.kind === "markdown" || document.kind === "text"} />
        ) : null}
      </div>
    </section>
  );
}
