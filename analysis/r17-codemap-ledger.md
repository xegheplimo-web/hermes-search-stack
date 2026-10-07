# R17 — Codemap value ledger

Sếp directive (2026-10-08): đo xem Codemap có **giảm recon, giảm đọc thừa, bắt được blast-radius/test-gap** không.
Instrumented từ Round 0. Graph: `.code-review-graph/graph.db` @ `cee297d` (refreshed 2026-10-08 06:43). CRG v2.3.9, embeddings=0 (FTS mode).

## Round 0 / RECON (2026-10-08)

**Queries run:** `update --brief` ×1, `status` ×1, `architecture` ×1, `flows` ×1, `search` ×13 (provider, source, rss, youtube, budget, fanout, worker, pool, trust, dedupe, merge, probe, revise), `file_summary` ×7 (protocols, standalone, engine, config, tools, deep_research, trust), + grep-defs (engine methods, mcp tools).

**Files actually read (source):** `tests/test_providers.py` (70L), `gateway/protocols.py` (57L full), `pyproject.toml` (14L), `gateway/config.py` (119L window), `gateway/mcp/tools.py` (40L window), `requirements-gateway.txt` (8L). ≈ **6 files / ~310 lines**.

**Blind-read alternative (no codemap):** gateway tree + `engine.py` (1083L) + `standalone.py` (271L) + `config.py` (243L) + `tools.py` (692L) + `deep_research.py` (438L) + trust/pool/ultra/planner ≈ **10+ files / 4k+ lines**. Estimated reading avoided ≈ **90%**.

**Accuracy log:**
- ✓ `search fanout` → `deep_research.py::build_fanout` (line 361) — correctly identified as eval-only helper, avoided mistaking it for engine fan-out.
- ✓ `file_summary` line ranges exact — spot-checked: protocols.py `SearchItem` 16–22, `ExtractItem` 26–32 vs source ✓.
- ✓ "provider" search surfaced `tests/test_providers.py` (vn_geo, R14-D) — not a false positive: revealed the house provider pattern; confirmed `gateway/providers/` absent.
- ⚠ Caveats carried (not hit this round): FTS-only search (embeddings=0); JS/TS flow recall weaker (irrelevant — R17 = gateway/python only).
- FP/FN: **0 / 0** so far.

**Blast-radius / test-gap catches:** n/a this phase (pre-diff). W1–W3 entries appended per wave.

## W1 (append khi xong)
## W2 (append khi xong)
## W3 (append khi xong)
