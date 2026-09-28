"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { clsx } from "clsx";
import type { DocumentContent, Highlight } from "../../lib/api";
import { centerInScroller } from "../../lib/scroll";

type Block = { start: number; end: number; text: string };

/** Group consecutive non-blank lines into blocks (paragraphs, tables, lists). */
export function toBlocks(lines: DocumentContent["lines"]): Block[] {
  const blocks: Block[] = [];
  let current: DocumentContent["lines"] = [];
  const flush = () => {
    if (current.length) {
      blocks.push({ start: current[0].line, end: current[current.length - 1].line, text: current.map((line) => line.text).join("\n") });
      current = [];
    }
  };
  for (const line of lines) {
    if (line.text.trim()) current.push(line);
    else flush();
  }
  flush();
  return blocks;
}

function highlightedLines(highlights: Highlight[]): Set<number> {
  return new Set(highlights.flatMap((item) => item.lines));
}

export function TextView({ content, highlights, nonce, markdown }: { content: DocumentContent; highlights: Highlight[]; nonce: number; markdown: boolean }) {
  const blocks = useMemo(() => toBlocks(content.lines), [content.lines]);
  const marked = useMemo(() => highlightedLines(highlights), [highlights]);
  const firstMark = useRef<HTMLDivElement>(null);
  const firstIndex = blocks.findIndex((block) => [...marked].some((line) => line >= block.start && line <= block.end));

  useEffect(() => {
    centerInScroller(firstMark.current);
  }, [nonce, firstIndex]);

  return (
    <div className="h-full overflow-auto bg-white px-6 py-5">
      <div className="mx-auto max-w-3xl space-y-3">
        {blocks.map((block, index) => {
          const isMarked = [...marked].some((line) => line >= block.start && line <= block.end);
          return (
            <div
              key={block.start}
              ref={index === firstIndex ? firstMark : undefined}
              data-lines={`${block.start}-${block.end}`}
              className={clsx("rounded-md px-3 py-1.5 transition-colors", isMarked && "bg-amber-100 ring-2 ring-amber-400/70")}
            >
              {markdown ? (
                <div className="prose prose-sm prose-slate max-w-none prose-table:text-xs prose-th:bg-slate-50 prose-td:py-1">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                    {block.text}
                  </ReactMarkdown>
                </div>
              ) : (
                <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-slate-800">{block.text}</pre>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function SheetView({ content, highlights, nonce }: { content: DocumentContent; highlights: Highlight[]; nonce: number }) {
  const initial = highlights[0]?.page ?? content.sheets[0]?.number ?? 1;
  const [sheetNumber, setSheetNumber] = useState(initial);
  const firstMark = useRef<HTMLTableRowElement>(null);
  useEffect(() => setSheetNumber(initial), [initial, nonce]);

  const sheet = content.sheets.find((item) => item.number === sheetNumber) ?? content.sheets[0];
  const rows = new Set(highlights.filter((item) => item.page === sheet?.number).flatMap((item) => item.lines));
  const firstRow = sheet?.rows.find((row) => rows.has(row.row))?.row;

  useEffect(() => {
    centerInScroller(firstMark.current);
  }, [nonce, sheetNumber, firstRow]);

  if (!sheet) return <p className="p-6 text-sm text-slate-500">Classeur vide.</p>;
  const width = Math.max(...sheet.rows.map((row) => row.cells.length), 1);

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-slate-200 bg-white px-3 py-2" role="tablist">
        {content.sheets.map((item) => {
          const cited = highlights.some((highlight) => highlight.page === item.number);
          return (
            <button
              key={item.number}
              type="button"
              role="tab"
              aria-selected={item.number === sheet.number}
              onClick={() => setSheetNumber(item.number)}
              className={clsx("whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium", item.number === sheet.number ? "bg-brand-600 text-white" : "text-slate-600 hover:bg-slate-100", cited && item.number !== sheet.number && "ring-2 ring-amber-400")}
            >
              {item.name}
            </button>
          );
        })}
      </div>
      <div className="flex-1 overflow-auto bg-white">
        <table className="min-w-full border-collapse text-xs">
          <tbody>
            {sheet.rows.map((row) => (
              <tr key={row.row} ref={row.row === firstRow ? firstMark : undefined} className={rows.has(row.row) ? "bg-amber-100" : "odd:bg-slate-50/60"}>
                <th className="sticky left-0 w-10 border-r border-slate-200 bg-slate-100 px-2 py-1 text-right font-normal text-slate-400">{row.row}</th>
                {Array.from({ length: width }, (_, index) => (
                  <td key={index} className="whitespace-nowrap border-b border-slate-100 px-2 py-1 text-slate-700">
                    {row.cells[index] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
