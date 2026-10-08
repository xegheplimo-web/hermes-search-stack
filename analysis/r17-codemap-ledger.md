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

## W1

**W1A — providers foundation** (Cline · longcat-2.5-preview-free · dispatched 10:05 → done 10:22, EXIT=0)
- Deliverables: `gateway/providers/__init__.py` (91L: protocol + PROVIDERS + ProviderError + get_provider + build_registry) · `gateway/config.py` (+4 keys + `enabled_providers()`) · `tests/gateway/test_providers.py` (11 tests) · round-0 cards + 9 live-captured fixtures.
- Orchestrator verify (independent re-run): 11 scoped ✓ · **1290 passed, 1 skipped** full ✓ · ruff check+format ✓ → merged **PR #5** (`15e54d1`, squash; CI 6/6 green: lint · test×3 · Bandit · web · Devin Review pass).
- Review loop: Devin Review ×3 + Codex ×1 → orchestrator classified: **2 true positives** (zero-cap selects one provider; factory raise blocks healthy providers) → reviewfix same agent; 1 by-design (empty env → default = house `_env_str` convention); 1 duplicate. Threads replied, resolved post-merge.
- Route data: Cline 1st try, ~17' wall, 0 fails; scope discipline clean (3 files).

## W2 (append khi xong)
## W3 (append khi xong)
