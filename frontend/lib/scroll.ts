/**
 * Center an element inside its nearest scrolling ancestor.
 *
 * Unlike element.scrollIntoView, this scrolls only that one container: in Chromium a smooth
 * scrollIntoView elsewhere on the page (the chat following a new answer) cancels another
 * one in progress, which left the document viewer at the top instead of on the citation.
 */
export function centerInScroller(element: HTMLElement | null): void {
  if (!element) return;
  let container = element.parentElement;
  while (container) {
    const { overflowY, overflowX } = getComputedStyle(container);
    if (/(auto|scroll)/.test(overflowY + overflowX)) break;
    container = container.parentElement;
  }
  if (!container || typeof container.scrollTo !== "function") return;
  const box = container.getBoundingClientRect();
  const target = element.getBoundingClientRect();
  container.scrollTo({
    top: container.scrollTop + (target.top - box.top) - (box.height - target.height) / 2,
    left: container.scrollLeft + (target.left - box.left) - (box.width - target.width) / 2,
    behavior: "smooth",
  });
}
