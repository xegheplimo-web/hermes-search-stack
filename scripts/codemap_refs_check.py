#!/usr/bin/env python3
"""Crosscheck v3 — snippet = FIRST line after ref chip only; descriptive anchors
(comments/annotations, no code punctuation) are marked DESC and not counted as errors.
Binary files (db) marked BINARY.
Usage: python scripts/codemap_refs_check.py [codemap-file]  (default: newest analysis/codemap*.txt)
"""

import glob
import os
import re
import sys

REPO = r"C:/Users/atton/hermes-search-stack"
if len(sys.argv) > 1:
    CM = sys.argv[1]
else:
    _cands = glob.glob(os.path.join(REPO, "analysis", "codemap*.txt"))
    if not _cands:
        sys.exit("no analysis/codemap*.txt found — pass the codemap file as argv[1]")
    CM = max(_cands, key=os.path.getmtime)

CAND = {
    "app.py": ["gateway/app.py"],
    "admission.py": ["gateway/security/admission.py"],
    "chat_completions.py": ["gateway/openai/chat_completions.py"],
    "engine.py": ["gateway/core/engine.py"],
    "router.py": ["gateway/core/router.py"],
    "planner.py": ["gateway/core/planner.py"],
    "ultra.py": ["gateway/core/ultra.py"],
    "pool.py": ["gateway/core/pool.py"],
    "claims.py": ["gateway/core/claims.py"],
    "local_context.py": ["gateway/core/local_context.py"],
    "hermes-adapter.ts": ["web/src/lib/hermes-adapter.ts"],
    "route.ts": ["web/src/app/api/chat/route.ts"],
    "sse-transform.ts": ["web/src/lib/sse-transform.ts"],
    "events.ts": ["web/src/lib/events.ts"],
    "places-tool-ui.tsx": ["web/src/components/tools/places-tool-ui.tsx"],
    "places-map.tsx": ["web/src/components/tools/places-map.tsx"],
    "tools.py": ["gateway/mcp/tools.py"],
    "places.py": ["vn_geo/places.py"],
    "store.py": ["searchstore/store.py"],
    "vectors.py": ["searchstore/vectors.py"],
    "db.py": ["searchstore/db.py"],
    "README.md": ["web/README.md", "README.md"],
    "places.db": ["data/places.db"],
}
LEX_CHIP = re.compile(r"^([A-Za-z0-9_\-]+\.(?:py|ts|tsx|md|db|json)):(\d+)$")
LEX_INLINE = re.compile(r"([A-Za-z0-9_\-/]+\.(?:py|ts|tsx|md)):(\d+)")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\u00a0", " ").strip())


def load_lines(path: str):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n").split("\n")


def is_code_like(s: str) -> bool:
    """True if snippet looks like actual source text (not an annotation)."""
    if not s:
        return False
    if s.startswith(("//", "#")) and not re.search(r"[(){};=]", s[2:]):
        return False
    # pure annotation words like 'Wait time tracking', 'returns Plan{...}' w/o code punc
    return bool(re.search(r"[(){};=\[\]]|->|``|\bdef\b|\bimport\b|\bfrom\b|\bclass\b|\breturn\b", s))


def resolve(basename):
    out = []
    for c in CAND.get(basename, []):
        if os.path.isfile(os.path.join(REPO, c)):
            out.append(c)
    return out


def main():
    cm = load_lines(CM)
    refs = []
    for i, ln in enumerate(cm):
        m = LEX_CHIP.match(ln.strip())
        if m:
            # snippet = first non-empty line after ref
            snip = ""
            j = i + 1
            while j < len(cm):
                s = cm[j].strip()
                if s and not LEX_CHIP.match(s):
                    snip = s
                    break
                if not s:
                    j += 1
                    continue
                break
            refs.append((f"L{i + 1}", m.group(1), int(m.group(2)), snip))
        for m2 in LEX_INLINE.finditer(ln):
            if "/" in m2.group(1):
                refs.append((f"L{i + 1}", os.path.basename(m2.group(1)), int(m2.group(2)), "<inline>"))
    # dedupe
    seen = set()
    uniq = []
    for r in refs:
        k = (r[1], r[2], r[3])
        if k not in seen:
            seen.add(k)
            uniq.append(r)

    ok = desc = diff = oob = miss = 0
    print(f"TOTAL REFS: {len(uniq)}")
    for label, fname, lno, snip in uniq:
        cands = resolve(fname)
        if not cands:
            print(f"[MISS-FILE] {label} {fname}:{lno}")
            miss += 1
            continue
        if fname.endswith(".db"):
            print(f"[DESC] {label} {fname}:{lno} (binary — not comparable)")
            desc += 1
            continue
        if snip == "<inline>" or not is_code_like(snip):
            print(f"[DESC] {label} {fname}:{lno} desc-anchor: {snip[:80]!r}")
            desc += 1
            continue
        best = None
        for c in cands:
            lines = load_lines(os.path.join(REPO, c))
            if lno > len(lines):
                best = best or (c, "OOB", f"EOF={len(lines)}")
                continue
            window = norm(" ".join(lines[max(0, lno - 3) : min(len(lines), lno + 2)]))
            if norm(snip) in window:
                best = (c, "OK", "")
                break
            else:
                a = norm(lines[lno - 1])
                best = (c, "DIFF", f"want~{norm(snip)[:85]!r} got@line~{a[:85]!r}")
        c, st, det = best
        if st == "OK":
            print(f"[OK]   {label} {fname}:{lno} -> {c}")
            ok += 1
        elif st == "OOB":
            print(f"[OOB]  {label} {fname}:{lno} -> {c} ({det})")
            oob += 1
        else:
            print(f"[DIFF] {label} {fname}:{lno} -> {c}\n       {det}")
            diff += 1
    print("=" * 70)
    print(f"SUMMARY: ok={ok} desc={desc} diff={diff} oob={oob} miss={miss}")
    sys.exit(1 if (diff or oob or miss) else 0)


if __name__ == "__main__":
    main()
