# R17 — Multi-source providers (keyless) — round control

**Status: ✅ R17 DONE (2026-10-08) — W1A ✓ (#5+#6) · W1B ✓ (#7) · W2 ✓ (#8) · W3A ✓ (#9) · W3B ✓ (#10). main @ `3bf8c5d`: full suite 1343 passed/1 skipped · ruff clean · bandit Medium 0/High 0 · live smoke 4/4 providers + engine deep fan-out ✓ · CRG detect-changes low risk.** Scope source: `analysis/r17-providers-plan.md`.
Orchestrator: Hermes (lead-orchestrator 1.4.4 + hermes-codemap v1.0.0 + CRG v2.3.9). Owner: Sếp.

## Process (chốt 2026-10-08 — codemap-integrated)

ROUND 0/RECON → `codemap:auto` (architecture khi cần) → ANALYZE (`execution`/`dependency`) → IMPACT (dependency + impact radius)
→ PLAN/SPLIT/ROUTE → DEVIN/CLINE/OPENCODE → IMPLEMENT → `codemap:change` (diff / blast radius / affected flows / test gaps)
→ SOURCE VERIFY → TEST/LINT/TYPECHECK/BUILD → FINAL VERIFY.

**Invariants (giữ cứng):** Codemap ≠ truth, CRG ≠ truth — *Graph locates; source establishes implementation; tests/runtime establish behavior.*
Lead orchestrator consumes ONLY the codemap object (mode/scope/confidence/architecture/execution/dependencies/change_impact/recommended_reads/verification) — never raw tool names.

## Verified current state @ cee297d (R17 RECON, 2026-10-08)

- `gateway/protocols.py` (frozen, r8): `SearchItem` / `ExtractItem` / `EvidenceItem` / `SearchBackend` protocol (search/extract/ping).
- `gateway/backends/`: `standalone.py` (271L; keyless Exa→Parallel chains; httpx lazy-imported; "never used in tests"), `hermes_bridge.py`, `stub.py`.
- `gateway/core/`: `engine.py` (1083L; `Engine.run/run_iter`; seams `_safe_search:840`, `_ultra_sub_searches:615`, `_ultra_extract:653`, `_items_to_evidence:877`, `_trust_order:896`), `pool.py` (**B2 worker pool — đã có → R17 order-dependency satisfied**), `ultra.py`, `planner.py`, `router.py`, `synthesis.py`, `claims.py`, `cache.py`, `local_context.py`.
- `gateway/config.py`: `GatewayConfig` dataclass + `HERMES_GATEWAY_*` env pattern; deep knobs: `deep_search_queries=3`, `deep_extract=8`, `pool_size=4`, `request_deadline_s`.
- `gateway/mcp/tools.py`: 7 tools + `FROZEN_TOOL_PARAMS` (surface frozen — R17 changes must be additive/back-compat).
- `trust.py`: `score_sources` / `host_overrides` / `_merge_ledger` — trust ordering entry point.
- `deep_research.py`: eval/CLI module (`build_plan`/`build_fanout` + check suite; FANOUT_MIN/MAX = eval budget concept only — NOT engine fan-out).
- House pattern (R14-D): `vn_geo/providers/` = PROVIDERS dict + `get_provider()` + Protocol class + hermetic tests (`tests/test_providers.py`). R17 mirrors this pattern in `gateway/providers/`.
- Deps (`requirements-gateway.txt`, exact pins): fastapi · uvicorn[standard] · httpx · mcp. No XML/feed deps.

## Decisions (technical — locked 2026-10-08)

1. **YouTube v1 = pure-HTTP timedtext, no yt-dlp.** Keyless/CI-safe; failure → `[]` + warning (per-source isolation). yt-dlp local tier only if ledger shows transcript coverage is a real blocker.
2. **RSS = minimal in-repo parser** (stdlib `xml.etree`, hardened: reject DOCTYPE/DTD, namespace-strip, size caps). No new deps (feedparser would add sgmllib3k transitively against the 4-pin policy). Revisit via ledger if malformed feeds fail in practice.
3. **Budget defaults:** `providers_enabled=false` (default = current behavior, byte-compatible); when on: `providers_max=4`, per-provider timeout 8s, 1 call/provider/query; deep path only; tune with harness.
4. **Caller evidence:** `hermes_research(context=[{title,url,content}])` additive optional param; entries become EvidenceItems (after fetched evidence), same claims verification; `hermes_social(query, platform)` thin surface (stretch — W3).
5. **Interfaces frozen first:** `analysis/r17-interfaces.md` v1.0 — W1 builds against it; deviations = change request (orchestrator-approved).

## Waves

| Wave | Scope | Depends | Agent | Gates |
|---|---|---|---|---|
| W1A | foundation: `gateway/providers/` registry + protocol + config keys + hermetic tests | interfaces v1.0 | TBD at dispatch | ruff + pytest hermetic + default-off regression suite |
| W1B | core fan-out: engine deep-path fan-out over pool (B2), per-source isolation | W1A | TBD at dispatch | same + fan-out covered by hermetic tests |
| W2 | providers: refactor exa/parallel/jina out of `standalone.py` + add v2ex/bilibili/youtube/rss | W1A | TBD at dispatch | same + standalone byte-compat tests green |
| W3 | `context` input + MCP surface + `hermes_social` + docs + eval corpus multi-source cases + live E2E | W1B, W2 | TBD at dispatch | full CI + live smoke + codemap:change verdict |

Wave order (Sếp chốt 2026-10-08): **W1A → (W1B ∥ W2) → W3 → full verify.** W1B and W2 are independent of each other (both only need W1A).

Round law: agent done ≠ done — orchestrator verifies each wave (source + tests + runtime evidence) before merge.

## Measurement (codemap value — Sếp directive)

Ledger: `analysis/r17-codemap-ledger.md` — per phase: queries run, files located vs read, FP/FN, blast-radius/test-gap catches.
Targets: recon reads ↓ vs pre-codemap rounds; zero silently-missed callers/tests.

## Risks

- `standalone.py` refactor must not change behavior (hermetic tests can't prove live parity → live smoke at W3).
- YouTube timedtext fragility (mitigated: isolation + warning; revisit trigger defined in Decisions §1).
- Budget tuning needs harness (latency-bound, keyless).
- JS/TS graph recall weaker — `web/` NOT in R17 scope (no impact).
