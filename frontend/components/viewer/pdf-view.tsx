"use client";

// Loaded only in the browser (see document-viewer.tsx): pdf.js needs DOM and workers.
import { ChevronLeft, ChevronRight, Loader2, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import type { Highlight } from "../../lib/api";
import { centerInScroller } from "../../lib/scroll";

// react-pdf requires the worker to be configured in the module that renders PDFs.
pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

type Props = {
  fileUrl: string;
  page: number;
  highlights: Highlight[];
  nonce: number;
  onPageChange: (page: number) => void;
};

const ZOOM_STEPS = [0.75, 1, 1.25, 1.5, 2];

export default function PdfView({ fileUrl, page, highlights, nonce, onPageChange }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const firstMark = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [zoomIndex, setZoomIndex] = useState(1);
  const [pageCount, setPageCount] = useState<number | null>(null);
  const [rendered, setRendered] = useState(0);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    // contentRect already excludes the padding: the page takes the full inner width.
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.floor(entry.contentRect.width))));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const boxes = highlights.find((item) => item.page === page)?.boxes ?? [];

  // Once the highlighted page has rendered, bring the first highlighted line into view.
  useEffect(() => {
    if (rendered && boxes.length > 0) centerInScroller(firstMark.current);
    else if (rendered) container.current?.scrollTo({ top: 0 });
  }, [rendered, nonce, page, boxes.length]);

  const go = useCallback(
    (target: number) => {
      if (pageCount && target >= 1 && target <= pageCount) onPageChange(target);
    },
    [pageCount, onPageChange],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(event.target.tagName);
      if (typing) return;
      if (event.key === "ArrowLeft") go(page - 1);
      if (event.key === "ArrowRight") go(page + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, page]);

  const highlightedPages = highlights.filter((item) => item.boxes.length > 0).map((item) => item.page);

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-200 bg-white px-3 py-2 text-sm">
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => go(page - 1)} disabled={page <= 1} className="rounded-md p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-40" aria-label="Page précédente">
            <ChevronLeft className="h-4 w-4" aria-hidden />
          </button>
          <label className="flex items-center gap-1 text-slate-600">
            <span className="sr-only">Page</span>
            <input
              key={page}
              type="number"
              min={1}
              max={pageCount ?? undefined}
              defaultValue={page}
              onKeyDown={(event) => {
                if (event.key === "Enter") go(Number((event.target as HTMLInputElement).value));
              }}
              className="w-14 rounded-md border border-slate-200 px-1.5 py-0.5 text-center"
            />
            <span>/ {pageCount ?? "…"}</span>
          </label>
          <button type="button" onClick={() => go(page + 1)} disabled={pageCount !== null && page >= pageCount} className="rounded-md p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-40" aria-label="Page suivante">
            <ChevronRight className="h-4 w-4" aria-hidden />
          </button>
        </div>
        {highlightedPages.length > 1 && (
          <div className="hidden items-center gap-1 text-xs text-slate-500 md:flex">
            Passages cités :
            {highlightedPages.map((number) => (
              <button key={number} type="button" onClick={() => go(number)} className={number === page ? "rounded bg-amber-200 px-1.5 font-semibold text-amber-900" : "rounded px-1.5 hover:bg-slate-100"}>
                p. {number}
              </button>
            ))}
          </div>
        )}
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => setZoomIndex((index) => Math.max(0, index - 1))} disabled={zoomIndex === 0} className="rounded-md p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-40" aria-label="Réduire">
            <Minus className="h-4 w-4" aria-hidden />
          </button>
          <span className="w-11 text-center text-xs text-slate-500">{Math.round(ZOOM_STEPS[zoomIndex] * 100)} %</span>
          <button type="button" onClick={() => setZoomIndex((index) => Math.min(ZOOM_STEPS.length - 1, index + 1))} disabled={zoomIndex === ZOOM_STEPS.length - 1} className="rounded-md p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-40" aria-label="Agrandir">
            <Plus className="h-4 w-4" aria-hidden />
          </button>
        </div>
      </div>
      <div ref={container} className="flex-1 overflow-auto bg-slate-100 p-4">
        <Document
          file={fileUrl}
          onLoadSuccess={({ numPages }) => setPageCount(numPages)}
          loading={<Centered>Chargement du document…</Centered>}
          error={<Centered spinning={false}>Impossible d’afficher ce PDF.</Centered>}
        >
          <div className="relative mx-auto w-fit shadow-lg">
            <Page
              key={`${page}-${zoomIndex}`}
              pageNumber={page}
              width={width * ZOOM_STEPS[zoomIndex]}
              onRenderSuccess={() => setRendered((count) => count + 1)}
              loading={<Centered>Chargement de la page…</Centered>}
            />
            <div className="pointer-events-none absolute inset-0" aria-hidden>
              {boxes.map(([x0, y0, x1, y1], index) => (
                <div
                  key={index}
                  ref={index === 0 ? firstMark : undefined}
                  className="citation-mark absolute"
                  style={{
                    left: `calc(${x0 * 100}% - 2px)`,
                    top: `calc(${y0 * 100}% - 1px)`,
                    width: `calc(${(x1 - x0) * 100}% + 4px)`,
                    height: `calc(${(y1 - y0) * 100}% + 2px)`,
                  }}
                />
              ))}
            </div>
          </div>
        </Document>
      </div>
    </div>
  );
}

function Centered({ children, spinning = true }: { children: string; spinning?: boolean }) {
  return (
    <div className="flex h-64 items-center justify-center gap-2 text-sm text-slate-500">
      {spinning && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </div>
  );
}
