/**
 * Source-chip extraction (heuristic v1).
 *
 * The backend replies in markdown. We collect links from:
 *  1. A trailing "sources" section, if present — a heading or bolded label like
 *     "Nguồn:", "Nguồn tham khảo:", "Sources:", "References:" near the end.
 *     In that zone both `[label](url)` links and bare URLs count.
 *  2. Otherwise, all inline `[label](url)` links in the message body.
 *
 * Chips are labelled by hostname (www. stripped) and deduped by URL.
 */

export type SourceLink = { url: string; label: string };

const INLINE_LINK_RE = /\[([^\]]*)\]\((https?:\/\/[^)\s]+)[^)]*\)/g;
const BARE_URL_RE = /https?:\/\/[^\s<>"'|\]]+/g;

// Label-only lines like "Nguồn:", "**Nguồn tham khảo:**", "## Sources",
// "1. Tham khảo:" — the label must be alone on the line (colon/bold any order).
const SECTION_RE =
  /(?:^|\n)\s*(?:#{1,6}\s*|\d+[.)]\s*|[-*+]\s+)?(?:\*\*|__)?\s*(nguồn(?:\s+tham\s+khảo)?|sources?|references?|tham\s+khảo)\b[\s:*_]*(?=\n|$)/iu;

const TRAILING_PUNCT = /[.,;:!?)\]]+$/;

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function collect(
  text: string,
  seen: Set<string>,
  out: SourceLink[],
  max: number,
  includeBare: boolean,
) {
  for (const m of text.matchAll(INLINE_LINK_RE)) {
    if (out.length >= max) return;
    const url = m[2].replace(TRAILING_PUNCT, "");
    if (seen.has(url)) continue;
    seen.add(url);
    out.push({ url, label: m[1].trim() || hostname(url) });
  }
  if (!includeBare) return;
  for (const m of text.matchAll(BARE_URL_RE)) {
    if (out.length >= max) return;
    const url = m[0].replace(TRAILING_PUNCT, "");
    if (seen.has(url)) continue;
    seen.add(url);
    out.push({ url, label: hostname(url) });
  }
}

export function extractSources(markdown: string, max = 8): SourceLink[] {
  const out: SourceLink[] = [];
  const seen = new Set<string>();
  const section = SECTION_RE.exec(markdown);

  if (section) {
    // Links inside the dedicated sources section (markdown or bare URLs)…
    collect(markdown.slice(section.index), seen, out, max, true);
    // …plus inline links above it that aren't part of the section itself.
    collect(markdown.slice(0, section.index), seen, out, max, false);
  } else {
    collect(markdown, seen, out, max, false);
  }
  return out;
}
