"use client";

import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

// Course material (and so answers and quotes) contains LaTeX such as $H_0$ or $p \leq \alpha$
// and Markdown emphasis. Render it inline: no paragraphs, links, or images.
const inline = { p: ({ children }: { children?: ReactNode }) => <>{children}</> };

/** A quote cut in the middle of **bold** leaves an unmatched marker: drop them all then. */
export function balanceEmphasis(text: string): string {
  return (text.match(/\*\*/g)?.length ?? 0) % 2 === 1 ? text.replaceAll("**", "") : text;
}

export function InlineText({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath]}
      rehypePlugins={[rehypeKatex]}
      components={inline}
      // Keep inline content only; block elements are unwrapped to their text.
      disallowedElements={["a", "img", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "table", "hr"]}
      unwrapDisallowed
    >
      {balanceEmphasis(text)}
    </ReactMarkdown>
  );
}
