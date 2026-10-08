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

## W1B — engine fan-out (Devin · 10:45 → 11:03, EXIT=0; fix round 11:08 → 11:17, EXIT=0)
- Deliverables: `gateway/providers/fanout.py` (NEW, 59L) · `gateway/core/engine.py` (additive deep-branch call-site + `_provider_fanout`; targets = probe query + sub-queries deduped) · `tests/gateway/test_fanout.py` (NEW, 9 tests).
- Verify: 9 scoped ✓ · regression 13 ✓ · **1301 passed, 1 skipped** ✓ · ruff ✓.
- Review loop: Devin ×4 + Codex ×3 (dups) → **2 TP** (full-probe evicts provider evidence; shared provider instances across workers) + 1 partial (timeout → W2) + 1 wontfix (warnings order) → Devin fix round: `_PROVIDER_EVIDENCE_RESERVE=2` + fresh registry/worker (+2 tests). Re-verify: 11 scoped · **1303 passed, 1 skipped** · ruff ✓. Merged **PR #7** (`2c671b9`; 7/7 threads resolved; CI green).
- Route data: Devin 1st try ~18' + fix ~9'; scope clean.

## W2 — providers (Cline · attempt-1 killed (15' no-write spiral) → v2 10:59 → 11:11, EXIT=0; fix1 in flight)
- Attempt-1: killed at 15' (221KB log, 0 file writes — fixture-format deliberation spiral). Card v2 (pre-decided fixture facts + write-first budget) → all 11 files written within ~5'.
- Deliverables: `_http.py` + 7 providers + `__init__` registration + 14 hermetic tests + planned W1A test rename; parallel fixture recaptured intact by orchestrator (capture-time truncation).
- Verify: 14 scoped ✓ · regression 13 ✓ · **1306 passed, 1 skipped** ✓ · ruff ✓ · registry = 7 ✓.
- CI Bandit B314 (ET.fromstring) → orchestrator fix: defusedxml lazy import + pin `defusedxml==0.7.1` (requirements-gateway.txt) → CI green.
- Review loop: Codex ×2 → **2 TP** (ProviderError contract wrap at provider boundary; bilibili `description` precedence) → fix1 dispatched (Cline, in flight).

## W3A — caller context + MCP surface (Devin · build → fix3)
- Deliverables: `gateway/protocols.py` (EvidenceItem.origin) · `gateway/core/engine.py` (run/run_iter context param; `_append_context_evidence`; SourceRef.origin) · `gateway/mcp/tools.py` (hermes_research context + origin echo; NEW `hermes_social` 8th tool; FROZEN_TOOL_PARAMS additive) · `tests/gateway/test_w3_context.py` (NEW).
- Verify: 68→72 scoped · full **1339→1343 passed, 1 skipped** · ruff clean (252) → merged **PR #9** (`f1a5fdf`) after fix3.
- Review loop PR #9: Devin ×2 + Codex ×2 → **3 TP** (context lost on spent deadline [dup]; caller URL overwrote fetched trust scores; **P1** context-scoped requests bypass the query cache — read AND publish) → fix3 same agent (+4 tests) → 7/7 threads resolved.

## W3B — eval corpus + docs (Cline · build → fix1)
- Deliverables: `evals/r9/corpus_v1.jsonl` (+12 multi-source cases vn-069..080, append-only) · `evals/r9/README.md` (80) · `AGENTS.md` (Gateway providers section).
- Verify: 80 valid JSON, old 68 byte-untouched, holdout intact (**vn-075 joins modulo-5 → 72 probes**), dry-run 72/72 · full **1321 passed** · ruff clean (251).
- CI caught: corpus-size oracle still 68 → orchestrator integration fix (`656ab8d`). Review loop PR #10: Devin ×3 + Codex ×1 → **3 TP** (variants dropped clauses; vn-071 address≠tax-code; vn-072 manufacturer range) + 1 dup-fixed + 1 by-design → fix1 same agent (`9cebda0`) → 7/7 threads resolved → merged **PR #10** (`3bf8c5d`).
- Live smoke: **4/4 providers live** (v2ex/bilibili/rss/youtube) · engine deep smoke: provider hits in evidence (reserve slots) ✓.

## Final verify (2026-10-08) — R17 DONE
- main @ `3bf8c5d`: full suite **1343 passed, 1 skipped** · ruff clean (252 files) · bandit CI-scope Medium 0/High 0 · CI remote all green across 10 PRs.
- Live: 4/4 provider smoke + engine deep fan-out smoke (2 provider hits survive cap).
- Post-close infra incident (orchestrator-owned, recovered): `git worktree remove --force` followed the `.venv` junctions into the repo venv → repo `.venv` gutted (python.exe/Lib gone) while the search-gateway kept serving zombie from RAM. Recovery: stopped `HermesSearchGateway`, killed its pythonw tree, unlinked junctions, rebuilt venv (Python 3.14 uv-managed: `uv venv --python 3.14` + dev/gateway requirements), full suite re-run **1343 passed, 1 skipped** (baseline equal), gateway restarted → `/healthz` ok. Lesson → lead-orchestrator 1.4.6 (never `git worktree remove` a worktree with junctions; unlink first).
