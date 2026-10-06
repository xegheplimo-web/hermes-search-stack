# Round 7 — verification (integration wave)

**Status: SHIPPED ✅** · Verified: 2026-10-06 · Owner: Hermes (Lead Orchestrator)
Contract: `analysis/r7-interfaces.md` (frozen) · Rollback point: `main` @ `8032c65`
Goal (backlog `analysis/00-FINAL-features.md` §7): make the R6 quality/speed wave operational — glue module + skill wiring + docs, then acceptance E2E.

## 1. Merges (Hermes-owned git)

| Commit | Task | What |
|---|---|---|
| `285d58e` | R7-A | `research_pack.py` — `research_pack.v1` builder + put-gate mirror + 30 tests + 9 fixtures |
| `413927c` | R7-B | `skills/research/deep-research/SKILL.md` → v1.1.0 (⓪ cache check · depth checkpoint · trust rank · verify gate · publish) |
| `af4e227` | R7-C | README + SPEC refreshed to rounds 1–7 |
| `bd493e7` | Hermes | CI: bandit scope extended (+`research_pack.py`, `fact_check.py`, `deep_research.py`, `verify_deep_research.py` — all clean) |
| `6a48a3f` | Hermes | scoreboard snapshot — battery 175403 / keyless 175425 |

Skill sync: repo `SKILL.md` → profile — byte-identical (v1.1.0).

## 2. Gates (post-merge, local) — all green

- `pytest -o addopts="" -q` → **630 passed** (600 → +30 R7-A tests)
- `ruff check .` clean · `ruff format --check .` → 94 files clean
- `bandit … -ll` → RC=0 on the CI scope **and** on the four extended files
- CI + Security green on `main` through `6a48a3f`

## 3. Task ledger

| Task | Agent | Model | Outcome | Wall time | Fails / notes |
|---|---|---|---|---|---|
| R7-A | Devin CLI | Devin runtime default | `research_pack.py` (410 lines), 30 tests, 9 fixtures; gates clean | 13m17s (17:36:31→17:49:48) | 1 self-corrected test expectation (IEEE-754 `0.825→0.82`); documented interpretations: `--stdout --json` summary→stderr, integral ttl emitted as int, duplicate-URL trust join last-wins |
| R7-B | Cline | `longcat-2.5-preview-free` | SKILL.md +64/−5 → v1.1.0; frozen commands verbatim | 10m52s (→17:47:23) | none; scope-pure (only SKILL.md) |
| R7-C | OpenCode | `muse-spark-1.3-contributor-free` | README +17 / SPEC +11; round history 1–7 | 1m02s (→17:37:33) | none; one cosmetic edit self-reverted |
| R7-D | Hermes | opencode-go/deepseek-flash (default) | full verification + acceptance E2E (§5) | 17:49→18:23 | see §6 |

## 4. Functional CLI smokes (R7-A binary)

`build` (pass fixtures) → `verified=true`, trust_scored 2/3 · `info` → **PASS** · fail-verification → **REJECT** · `pack_reject.json` → **REJECT** · cross-compat: `answer_cache put` accepted the built pack → `get` round-trip `found=true`.

## 5. Acceptance E2E — live, fresh CLI sessions

**S1 — cache MISS → full wired workflow → publish** (`20261006_175548_fcde6e`, 14m09s, 40 tool calls, exit 0):
⓪ `answer_cache get` miss → fan-out searches → ledger (20 sources) → ② `depth_policy decide` → ④ `trust.py rank` (+manual overrides) → draft → `sources.py verify --strict` PASS → `verify_deep_research.py` PASS → ⑦ `fact_check.py` → `pass: true`, 0 unsupported / 0 conflicting (coverage 0.96) → ⑧ `research_pack.py build` (`verified: true`) → `answer_cache put` → `ok: true`.

**S2a — direct-answer control observation** (`20261006_181517_21e69c`, 45s, exit 0): the same question phrased as a quick Q&A did **not** trigger the deep-research skill, so it answered with a fresh 4-source search; cache untouched — by design (the cache is a skill-level step, not a global web-layer cache).

**S2b — cache HIT → serve** (`20261006_181816_857b3d`, 4m47s, 19 tool calls, exit 0): the long-report request found a fresh pack ("*the answer cache has a FRESH pack for exactly this query*"), served the cached report + 20 sources + `created_at`, and offered a refresh. Extra diligence inside the serve branch: md5-compared artifacts, spot-checked links.

**Cache counters:** absent before S1 → after S1 `packs=1, sources=20, hits=0` → after S2b **`hits=2`** (1 = orchestrator CLI probe, 1 = S2b session). Artifacts: `evidence/r7d/`, local logs `agent_logs/r7d-*`.

## 6. Deviations & notes

1. **S2a**: direct-answer sessions bypass the skill/cache by design — recorded as expected behavior, not a defect.
2. S1 normalized its prompt into the canonical query used for publish/serve (match = casefold + whitespace collapse; wording must stay identical):
   `Node.js LTS tháng 10/2026: bản LTS hiện hành; lịch phát hành và cửa sổ hỗ trợ của Node 20/22/24/26; thay đổi chính của Node.js 24 so với Node.js 22`
3. `fact_check` no_quote warnings (20) are non-blocking by design.
4. `REPORT.md` still carries the R1–R2 narrative → refresh is a candidate for the next wave.
5. Goong account activation still pending (external).

## 7. Regression batteries (fresh, post-merge)

T1 `verify_web_stack.py` → **PASS 9/9** (`results/battery_20261006_175403.*`) · T2 `test_keyless_fallback.py` → **PASS 6/6** (`results/keyless_20261006_175425.*`) · scoreboard refreshed (`6a48a3f`).

## 8. Verdict

Round 7 **done**: the R6 wave is wired and proven end-to-end (miss → publish → serve).
Backlog item `analysis/00-FINAL-features.md` §7 closed — the planned feature set is complete; operations continue via cron (vn-geo weekly) + CI.
