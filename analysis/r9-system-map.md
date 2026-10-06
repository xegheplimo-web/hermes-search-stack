# R9-A — System map, real E2E request path, measured baseline traces

**Repo:** `C:/Users/atton/hermes-search-stack` · **Date:** 2026-10-06 · **Gateway:** `http://127.0.0.1:8787` (live, backend `hermes`)
**Method:** read-only code audit + 8 live `POST /v1/chat/completions` calls (non-stream, ≥3 s pacing) + read-only inspection of `data/answers.db` / `data/searchstore.db` (WAL left untouched by the audit; only the server writes) + 1 MCP `tools/call` probe. Raw chat responses saved verbatim to `agent_logs/r9a_traces/NN_<slug>.json`. Wall times are client-side `perf_counter` around `httpx.post` (timeout 180 s).

---

## 1) Component inventory

Classification: **PRODUCTION-PATH** = participates in a real user request route (HTTP chat / health / MCP tool call on the live server). **UTILITY** = verification battery, benchmark, experiment, docs, historical artifact, or offline data-prep not invoked by a live request.

| Path | Role | Class | Evidence (file:line) |
|---|---|---|---|
| `gateway/__main__.py` | Entry point: `python -m gateway` → uvicorn serve; `--mcp-stdio` alt mode | PRODUCTION-PATH | `gateway/__main__.py:49` (`uvicorn.run(create_app(...))`) |
| `gateway/app.py` | FastAPI factory; mounts routers + `/mcp`, owns `/healthz`,`/readyz` | PRODUCTION-PATH | `gateway/app.py:127-211` (`create_app`, routes at 180-200, mount at 208) |
| `gateway/config.py` | `GatewayConfig` env-driven config (`HERMES_GATEWAY_*`), key resolution | PRODUCTION-PATH | `gateway/config.py:114-161`; consumed `app.py:134-137` |
| `gateway/protocols.py` | `SearchItem`/`ExtractItem`/`EvidenceItem`/`SearchBackend` shapes | PRODUCTION-PATH | `gateway/protocols.py:45-57`; consumed `gateway/core/engine.py:24` |
| `gateway/openai/chat_completions.py` | `POST /v1/chat/completions` handler (stream + non-stream) | PRODUCTION-PATH | `gateway/openai/chat_completions.py:133-152` |
| `gateway/openai/models.py` | `GET /v1/models` | PRODUCTION-PATH | `gateway/openai/models.py:15-28` |
| `gateway/openai/streaming.py` | SSE chunk builders + cache-text splitter | PRODUCTION-PATH | `gateway/openai/streaming.py:29-93`; used `chat_completions.py:18,99-125` |
| `gateway/openai/responses.py` | `POST /v1/responses` → 501 stub | PRODUCTION-PATH (stub route) | `gateway/openai/responses.py:11-23` |
| `gateway/security/auth.py` | Bearer/loopback guard on all `/v1/*` routes | PRODUCTION-PATH | `gateway/security/auth.py:28-49`; wired `app.py:153-156` |
| `gateway/security/rate_limit.py` | Per-key token bucket → 429 | PRODUCTION-PATH | `gateway/security/rate_limit.py:85-91`; wired `app.py:153,151` |
| `gateway/core/engine.py` | Orchestrator: cache→probe→depth→extract→synth→verify/publish | PRODUCTION-PATH | `gateway/core/engine.py:112-242` (`run_iter`); invoked `chat_completions.py:149` |
| `gateway/core/router.py` | Query markers + signals builder + `depth_policy` shim | PRODUCTION-PATH | `gateway/core/router.py:28-87`; used `engine.py:23,139-155,168-172` |
| `gateway/core/cache.py` | `GatewayCache` adapter over `AnswerCache` | PRODUCTION-PATH | `gateway/core/cache.py:13-31`; used `engine.py:272-274` |
| `gateway/core/synthesis.py` | OpenAI-compatible synth client (opencode-go `deepseek-flash`) | PRODUCTION-PATH | `gateway/core/synthesis.py:52-190`; used `engine.py:256-264,197` |
| `gateway/backends/__init__.py` | `create_backend` — `auto`→hermes, else standalone | PRODUCTION-PATH | `gateway/backends/__init__.py:21-47`; used `engine.py:248-250` |
| `gateway/backends/hermes_bridge.py` | Live backend on this host: stdio sidecar into Hermes venv | PRODUCTION-PATH | `gateway/backends/hermes_bridge.py:99-208`; `/readyz` reports backend `hermes`, worker alive |
| `gateway/backends/standalone.py` | Keyless direct-HTTP backend (`auto` fallback when no Hermes) | PRODUCTION-PATH (dormant fallback on this host) | `gateway/backends/standalone.py:1-42`; reachable via `backends/__init__.py:43-46` |
| `gateway/backends/stub.py` | Canned backend for tests | UTILITY | only instantiated when `backend=="stub"` (`backends/__init__.py:24-25`); live config resolves `auto`→hermes |
| `gateway/bridge/worker.py` | Sidecar worker: managed search + keyless extract ring inside Hermes venv | PRODUCTION-PATH | `gateway/bridge/worker.py:84-141` (search), `144-180` (extract); spawned `hermes_bridge.py:237-245` |
| `gateway/mcp/server.py` | MCP server build + stdio/streamable-http adapters | PRODUCTION-PATH (MCP surface) | `gateway/mcp/server.py:19-60`; mounted `app.py:139-143,202-208` |
| `gateway/mcp/tools.py` | 6 frozen MCP tools over engine/repo modules | PRODUCTION-PATH (MCP surface — see §5 #1: 2 of 6 broken) | `gateway/mcp/tools.py:97-402` |
| `depth_policy.py` | Fast/deep decision table (frozen weights, threshold 0.45) | PRODUCTION-PATH | `depth_policy.py:148-188` (`needs_depth`); via `router.py:64-68` |
| `searchstore/answer_cache.py` | Verified-answer cache (`data/answers.db`) | PRODUCTION-PATH (on paper — see §5 #1: live path dead) | `searchstore/answer_cache.py:254-384`; via `gateway/core/cache.py:17-23` |
| `searchstore/db.py` | SQLite schema/FTS5 for document store | PRODUCTION-PATH (MCP tools only — NOT the HTTP chat path) | `searchstore/db.py:35-38` FTS table; opened via `tools.py:363` |
| `searchstore/store.py` | `SearchStore` FTS/hybrid query API | PRODUCTION-PATH (MCP tools only) | `searchstore/store.py:232-291`; used `tools.py:364,467` |
| `searchstore/vectors.py` | sqlite-vec tier for `hybrid` mode | UTILITY (dead on MCP: `hybrid`/`vector` rejected at `tools.py:342-347`) | `tools.py:342-353` |
| `searchstore/adapters.py`, `cli.py`, `__main__.py` | Ingest adapters + CLI | UTILITY (offline ingest; not in request path) | — |
| `vn_news.py` | VN news RSS ring → store ingest/query | PRODUCTION-PATH (MCP `hermes_vn(kind=news)` only; not in HTTP chat path) | `vn_news.py:285-319`; invoked `tools.py:391-392,410-448` |
| `trust.py` | Source trust scoring | PRODUCTION-PATH (deep path only) | `trust.py:243` `score_sources`; invoked `engine.py:344-356` |
| `fact_check.py` | Citation-coverage verification gate | PRODUCTION-PATH (deep path only, `judge="off"`) | `fact_check.py:575-695` `run_check`; invoked `engine.py:376,409` |
| `research_pack.py` | `research_pack.v1` builder for cache publish | PRODUCTION-PATH (deep path only) | `research_pack.py:141-208`; invoked `engine.py:419-431` |
| `deep_research.py` | Deep-research workflow helper (plan/fanout/check) | UTILITY | zero gateway imports — `import` grep over `gateway/` hits only `depth_policy`,`trust`,`fact_check`,`research_pack` |
| `verify_web_stack.py`, `test_keyless_fallback.py`, `verify_deep_research.py` | R1–R2 live verification batteries | UTILITY | run manually; worker reuses their *call paths* (`worker.py:79-82`) but the files are never imported |
| `evals/` (incl. `evals/r9`) | Offline eval batteries + cases | UTILITY | — |
| `tests/`, `testsgateway/` (empty), `gatewaymcp/` (empty) | Unit tests / stray empty dirs | UTILITY | `testsgateway/`,`gatewaymcp/` contain no files |
| `vn_geo/` | Geo/business ETL (admin units, OSM POI, CKAN, Goong) | UTILITY (offline ingest into store; never called live) | MCP `hermes_vn` reads the *store* (`tools.py:451-474`), never imports `vn_geo` |
| `plugins/search-prefetch/` | Hermes-side prefetch plugin | UTILITY (Hermes-side; not imported by gateway) | — |
| `scripts/scoreboard.py`, `scripts/refresh_cron.py` | Scoreboard + weekly vn-geo refresh | UTILITY | — |
| `analysis/`, `evidence/`, `references/`, `results/`, `REPORT.md`, `SPEC.md` | Docs, frozen contracts, committed evidence | UTILITY | — |
| `data/` (gitignored) | `searchstore.db`, `answers.db`, `tmp/` runtime artifacts | runtime state (not code) | config defaults `config.py:124-125,189-191` |

**Headline shape of the production path:** HTTP chat and MCP share one `Engine` (`app.state.engine`, `app.py:137`). The live retrieval backend is the Hermes sidecar (`hermes` per `/readyz`). The HTTP chat path touches: auth/ratelimit → last-message extraction → AnswerCache get → Hermes worker (managed Perplexity search → keyless extract ring) → depth policy → synth LLM → (deep only) fact_check/research_pack/cache-put. **The HTTP path never queries `searchstore`/`vn_news`/`vn_geo`** — those are reachable only via the MCP tools.

---

## 2) Real E2E request path (gateway HTTP)

End-to-end for `POST /v1/chat/completions {"model":"hermes-search","messages":[...],"stream":false}` — each hop lists file:line, data passed, drops/truncations, and where time goes (code + measured §3).

**Step 0 — transport/auth.** uvicorn serves `create_app(config)` (`gateway/__main__.py:49`, `gateway/app.py:127-156`). Every `/v1/*` route is wrapped in `Depends(api_key_guard)` + `Depends(rate_limit_guard)` (`app.py:153-156`): with no `HERMES_GATEWAY_API_KEY`, only loopback peers pass (`security/auth.py:38-48`); a 20 rps token bucket keyed by bearer-hash or host (`security/rate_limit.py:61-68,85-91`). `/healthz`,`/readyz` are unprotected (`app.py:180-200`). Cost: sub-ms.

**Step 1 — request → query string.** `create_chat_completion` (`chat_completions.py:133-152`): rejects `model != "hermes-search"` with 404 (`137-138`), extracts **only the LAST user message** via `_last_user_query` (`139`, def `53-58`) — *dropped: all prior turns; multi-turn context never reaches the engine* (documented `chat_completions.py:3-5`; observed §3 #7). Non-stream → `engine.run(query)` (`149`); stream → `StreamingResponse(_stream_chat(...))` (`144-147`).

**Step 2 — cache lookup (~ms when it works).** `Engine.run_iter` (`engine.py:112-132`): `GatewayCache.get` (`core/cache.py:21-23`) → `AnswerCache.get` (`searchstore/answer_cache.py:345-384`). Key = `sha256(normalize_query + "\n" + scope)` (`answer_cache.py:117-124`); `normalize_query` = whitespace collapse + `casefold` — **diacritics preserved** (`117-119`). Fresh hit (`age_days ≤ ttl_days`) → serve pack's `answer_markdown` as `depth="cache"`, zero live calls (`engine.py:124-130`, `_result_from_pack` `440-466`). *Dropped on serve:* pack `verification`/`meta`; sources re-shaped (`445-455`). *Stale hit* → falls through with a warning (`131-132`). **Live status: this hop currently never hits and `put` never lands — see §5 #1.**

**Step 3 — probe search (~1-3 s of wall).** `_safe_search(query, fast_max_results=10)` (`engine.py:137,301-308`) → `HermesBridge.search` (`backends/hermes_bridge.py:130-144`) → `_call("search", {query, max_results})` (`183-208`): one JSON line on worker stdin, 60 s timeout (`103,185-200`), serialized under one lock (`186`) — **all gateway traffic funnels through ONE worker process** (spawn `237-245`, respawn cap 3 per 10 min `214-221`). Worker `_op_search` (`bridge/worker.py:108-141`): managed Perplexity via `tools.web_tools.web_search_tool` (`84-89`); on failure → keyless `search_with_failover("parallel",...)` (`92-96,129-141`). Rows → `SearchItem{title,url,description,position}` (`worker.py:36-45`); *dropped:* every vendor field except those four (published dates, favicons, scored ranks — anything else Perplexity returns is gone before the engine sees it).

**Step 4 — depth decision (~µs).** `query_markers` (`engine.py:139` → `router.py:28-43`): `comparative`/`multi_part`/`vn` regexes. `build_signals` (`engine.py:146-151`) → `decide` → `depth_policy.needs_depth` (`router.py:64-68` → `depth_policy.py:148-188`), deep when score ≥ 0.45 (`depth_policy.py:50,187`). **Notable:** `extract_char_totals` is never populated — the decision runs before extraction (`engine.py:146-151` omits the kwarg) → `char_sum=0` → the `+0.10` "tiny extract chars" rule fires on EVERY query (`depth_policy.py:177-179`) and the `−0.20` rich-evidence discount can never fire (`180-184`). Observed scores (inferred from §3 depths): fast singles ~0.10-0.25; `05` (comparative+multi_part) ≈ 0.65 → deep.

**Step 5 — evidence extraction (~2-8 s of wall).**
- *fast:* `urls = dedupe(probe)[:fast_extract=4]` (`engine.py:179`; caps `config.py:130-131`).
- *deep:* probe + ≤2 `split_query` sub-searches (`engine.py:165-172`; `router.py:71-87`), `≤deep_extract=8` URLs (`engine.py:173`; `config.py:133`), then `_trust_order` via `trust.score_sources` (`engine.py:339-364` → `trust.py:243`). Measured: all trust scores 0.20/tier=unknown on VN sources (§5 #6).
- `_extract_evidence` (`engine.py:310-337`) → `backend.extract(urls, char_limit=15000)` (`321`) → worker `_op_extract` (`worker.py:144-180`): managed `web_extract_tool` batch first (`154-160`), per-URL keyless ring `extract_with_failover("parallel",[url])` for misses (`172-176`; ring parallel→firecrawl→keenable→exa `99-105`). *Truncations:* `char_limit=15000` per page (`engine.py:321`, enforced again `worker.py:177-179`); items with `error`+empty content dropped (`332-334`). Zero evidence → snippet-only `EvidenceItem`s from search descriptions (`engine.py:176-183,70-74`).

**Step 6 — synthesis (dominant: ~5-15 s measured).** `Synthesizer.stream(query, evidence, deep=…)` (`engine.py:187-211` → `synthesis.py:135-170`): POST `{synth_base_url}/chat/completions` stream=true, `temperature=0.2` (`146-151`); target `https://opencode.ai/zen/go/v1`, model `deepseek-flash` (`config.py:21-22`); key `HERMES_GATEWAY_SYNTH_API_KEY`→`OPENCODE_GO_API_KEY` env→`<hermes_home>/.env` (`config.py:193-204`); auto `x-opencode-session` for opencode.ai (`synthesis.py:83-84`). Prompt: system contract (`synthesis.py:23-35`) + `Question:` + **raw evidence block inlined** — title+url+content each up to 15 k chars, `_evidence_block` (`38-43`), `_messages` (`46-49`). Deep adds the "structured/longer" suffix (`32-35`). Failure ⇒ `_iter_fallback` source list + `last_warning` (`synthesis.py:168-190`; engine warns `engine.py:201-209`). Server log shows exactly ONE synth POST per chat request (`agent_logs/r8-gateway-live3.log`, 20:28-20:33).

**Step 7 — deep-only verify & publish.** `_verify_and_publish` (`engine.py:366-438`): writes draft+ledger to `data/tmp/` (`380-407` — files confirmed present for traces 05/08) → `fact_check.run_check(draft, ledger, judge="off")` (`409`) — **mechanical checks only** (coverage ≥0.5, no missing_ids; `fact_check.py:601-671`) → on `exit==0`, `research_pack.build_pack` (`423-431` → `research_pack.py:141-208`) → `AnswerCache.put` (`436` → `answer_cache.py:281-341`). Any failure → warning, answer still served (`engine.py:411-417`). **Measured: the chain runs (artifacts written, gate passes offline on the same files) but no pack lands — §5 #1.**

**Step 8 — response assembly.** `EngineResult{answer_markdown, sources, depth, cached, reason, warnings, timings_ms}` (`engine.py:230-242`). The non-stream HTTP response keeps **only `answer_markdown`** + estimated usage (chars/4, `chat_completions.py:61-87`) — *dropped at the wire:* `sources`, `depth`, `reason`, `warnings`, `timings_ms` (sources visible only via the embedded `## Sources` list inside the text). `POST /v1/responses` → 501 (`responses.py:11-23`).

**Where the time goes (measured, §3):** fast queries ≈ 9-14 s total ≈ probe (~1-3 s, managed Perplexity) + extract ≤4 pages (~2-6 s, managed/keyless ring) + synth stream (~5-8 s, opencode relay). Deep ≈ 31 s ≈ 3 searches + ≤8 extracts + longer synth + verify. Cache-hit path would be ~ms — never observed (§5 #1).

**MCP surface (shares the same engine).** `app.mount("/", mcp_server.streamable_http_app())` (`app.py:202-208`; the child app owns `/mcp`; the parent lifespan runs the session manager, `app.py:106-124`). `build_mcp(engine)` registers the 6 frozen tools (`mcp/server.py:19-34` → `mcp/tools.py:97-402`):
- `hermes_search` / `hermes_extract` → `active_backend.search/extract` — resolved via `getattr(engine,"backend",None)` (`tools.py:59-63`), but `Engine` only defines `_backend` (`engine.py:89`) → always `None` → `{"results":[],"error":"no search backend available"}` (`tools.py:118-123,142-147`). **OBSERVED live: `tools/call hermes_search` → that exact error.**
- `hermes_research` → `engine.run(query, depth)` (`tools.py:161-197`) — the only MCP tool exposing `depth`/`warnings`/`elapsed_ms`; full §2 pipeline.
- `hermes_fact_check` → repo `fact_check.run_check` (mechanical by default; `judge=aux` opt-in via `HERMES_GATEWAY_FACT_JUDGE`) (`tools.py:199-331`).
- `hermes_store_query` → `SearchStore.search(mode="fts")` on `config.store_db` (`tools.py:333-371`); `hybrid`/`vector` rejected (`342-347`). **OBSERVED live: `"nghi dinh 168"` → 0 results (§4).**
- `hermes_vn` → `news` delegates to `vn_news.query` (`tools.py:391-392,410-448`); `admin/places/enterprises` → FTS filtered by provider-kind (`451-474`).

---

## 3) Measured traces

`/healthz` at start: `uptime_s = 2265.34` → at end: `uptime_s = 3495.01` (backend `hermes`, ready the whole run — untouched). Server log corroborates: exactly one synth POST to `opencode.ai` per request, zero HTTP errors (`agent_logs/r8-gateway-live3.log`).

Depth is **inferred** (the wire drops `depth`/`timings` — §2 step 8): `>4` evidence ids ⇒ deep (fast caps extracts at 4, `config.py:131`); wall ~30 s + `data/tmp/` artifacts corroborate the two deep runs.

| # | File | Query (lang) | Depth | Wall s | Srcs (cited/evid.) | Key facts in answer | Warnings/errors | Where latency entered |
|---|------|--------------|-------|--------|--------------------|---------------------|-----------------|-----------------------|
| 1 | `01_simple_vn_factual.json` | "Ngày Quốc khánh của Việt Nam là ngày nào?" (VN) | fast | 13.70 | 3/≤4 | 2/9/1945 declaration + Hiến pháp 2013 Điều 13 | none on wire | extract + synth |
| 2 | `02_nodsc_legal.json` | "nghi dinh 168 phat bao nhieu" (VN, no diacritics) | fast | 13.83 | 4 | NĐ 168/2024/NĐ-CP: no single fine; examples 18–22 tr.đ ô tô đèn đỏ; honest "tra toàn văn" | none on wire | extract + synth; **Perplexity folded the accent-less query fine** |
| 3 | `03_freshness_gold.json` | "giá vàng hôm nay bao nhiêu tiền" (VN, freshness) | fast | 9.15 | 3 | XAU/USD 4,378.23 (+0.84%); SJC 144.6/147.6 tr.đ/lượng; hedged on intraday | none on wire | synth |
| 4 | `04_local_business.json` | "quán phở ngon gần quận Hoàn Kiếm Hà Nội" (VN, local) | fast | 14.35 | 3 | Bát Đàn, Lý Quốc Sư, Thìn Bờ Hồ, Khôi Hói + Michelin Bib Gourmand + prices | none on wire | extract + synth |
| 5 | `05_en_mixed_compare.json` | "So sánh iPhone 16 Pro và Samsung Galaxy S25 Ultra: cái nào tốt hơn?" (EN-mixed, comparative) | **deep** (ids→8) | 30.80 | 5 cited / 8 evid | Structured VN comparison; honest caveat that benchmarks are Pro Max, not Pro | (dropped at wire; publish failed — §5#1) | 3 searches + 8 extracts + long synth + verify |
| 6 | `06_ambiguous.json` | "nó bao nhiêu tiền?" (VN, ambiguous) | fast | 9.42 | 2 | Honest "chưa đủ thông tin"; guessed interpretations (shopping dialogue, 1 CNY=3,947 đ) | none on wire | retrieval returned generic pages; synth hedged |
| 7 | `07_followup.json` | msgs: "Nghị định 168 quy định gì?" → asst → "còn mức phạt thì sao?" | fast | 12.55 | 2 | Answered NĐ168 fines anyway — **backend topicality, not context** (only last msg sent) | none on wire | extract + synth |
| 8 | `08_cache_repeat.json` | identical repeat of #5 | **deep again** (not cache) | 30.73 | 8 evid | Different answer text than #5 (11,975 vs 7,029 bytes) | cache never populated | full deep pipeline re-run |

Corroborating DB evidence (read-only): `data/answers.db` `packs` = **1 row** (a Node.js query, `created_at` 11:08 UTC — earlier R8 work), `hits` = 2 (both `source='cli'`, 18:14/18:18 — none `source='gateway'`), `answers.db-wal` mtime **19:43** (server boot) and untouched through my 20:28-20:33 runs despite two passing deep runs writing `data/tmp/gateway-*.md/.json` at 20:31 & 20:33.

---

## 4) Vietnamese text handling audit (code-level)

| Touchpoint | file:line | Behavior |
|---|---|---|
| Store FTS tokenizer | `searchstore/db.py:35-38` | `unicode61 remove_diacritics 2` — folds precomposed vowel marks **but NOT `đ`→`d`** (U+0111 has no Unicode decomposition). **OBSERVED on the live store** (`data/searchstore.db`, 1,285 `vn_news` docs): `bão`/`bao` both → 159 docs (vowel folding works); `định`→198 vs `dinh`→6; `đường`→204 vs `duong`→26; `đồng`→448 vs `dong`→30; `đà nẵng`→80 vs `da nang`→3; `nghị định`→25 vs `nghi dinh`→**0**. Accent-less VN recall on the store/MCP path is badly broken on `đ`-words only. |
| FTS MATCH passthrough | `searchstore/store.py:62-63,274-278` | Raw user query string handed to `MATCH ?` — FTS5 operators (`"`, `*`, `OR`, `NEAR`) live in user input; `sqlite3.Error` → `SearchStoreError`. No stemming/synonyms/abbrev expansion. |
| Answer-cache key | `searchstore/answer_cache.py:117-124` | `normalize_query` = `casefold` + whitespace collapse; **diacritics preserved** → `"Nghị định 168"` vs `"nghi dinh 168"` are different keys (cache fragmentation across accent spellings). Punctuation not stripped. |
| RSS text cleaning | `vn_news.py:110-113` | `_clean_text`: HTML tags→space (sentence boundaries kept), entities unescaped by the XML parser, whitespace collapsed; UTF-8 end-to-end. |
| VN date parsing | `vn_news.py:123-149` | RFC-822 → ISO-8601 → VN formats (assumed +07:00); unparseable → `""` (then `--days` filters drop the record, `vn_news.py:305-313`). |
| VN query markers | `gateway/core/router.py:14-25,28-43` | `vn` marker = diacritic chars OR function-word list (của/và/không/cho/trong/được/với/người/hôm nay/giá/tin tức/tại/thành phố/là gì/nào). `"nghi dinh 168 phat bao nhieu"` trips **neither** → `vn=False` (OBSERVED deterministically from the regexes on the exact trace-02 string). Marker is validated (`depth_policy.py:137-139`) but **never used in scoring** (`159-188`) — dead signal today. |
| Depth-term match | `depth_policy.py:52-88` | `DEEP_TERMS` VN entries carry diacritics (`"so sánh"`,`"phân tích"`,`"chi tiết"`,`"toàn diện"`,`"nghiên cứu sâu"`); casefolded **substring** match → `"so sanh"`/`"phan tich"` never count. |
| Multi-part split | `router.py:25,71-87` | `_SPLIT_RE` cuts on `?;!` and `and`/`và`/`then` — trace 05's `… và …` produced the second deep sub-query (deep adds `max_parts=deep_search_queries−1=2`, `engine.py:168-170`). |
| Synthesis prompt | `synthesis.py:23-49` | "Answer in the SAME LANGUAGE as the user's question"; evidence inlined raw, no normalization/transliteration — VN extracts pass through intact. Language fallback worked even for accent-less input (trace 02 answered in proper diacritics). |
| Trust scoring | `trust.py:158-170,243+,298-308` | Host normalization only (lowercase/`www.` strip). **OBSERVED:** every source in the deep run scored 0.20/tier=unknown (`low_trust` flags on ids 1,2,4,6,8… in reproduced `run_check`) — the curated tier table has no VN domains, so trust ordering is flat for VN content. |
| fact_check sentence split | `fact_check.py:132-136,247-291,601-610` | Prose split `(?<=[.!?])\s+`, ≥4-word sentences, `[n]` citation regex; VN sentences parse fine (coverage 0.96-0.98 on the real drafts). |
| research_pack | `research_pack.py:83-94,141-208` | URL normalization + field mapping only; query/answer pass through unmodified. |
| history/intent | `chat_completions.py:3-5,53-58` | Last user message only — follow-ups lose all context. OBSERVED trace 07: the engine saw only "còn mức phạt thì sao?" |
| `deep_research.py` | — | UTILITY; not imported by the gateway. |

### Gap list (OBSERVED = measured in a trace or live DB; SUSPECTED = code-reading only)

| Gap | Status | Evidence |
|---|---|---|
| FTS `đ` never folds → accent-less VN queries under-recall on the store path (MCP `hermes_store_query`/`hermes_vn`,`vn_news query`) | **OBSERVED** | live FTS counts above; `db.py:37`; MCP `hermes_store_query("nghi dinh 168")` → `{"results":[]}` |
| No-diacritics queries bypass the `vn` marker — and the marker is dead weight anyway | **OBSERVED** (regexes on trace-02 string + `depth_policy.py:137-188` marker unused) | `router.py:15-19` |
| Answer-cache can't dedupe accent/punctuation variants (`"nghi dinh 168"` vs `"nghi định 168"`) | SUSPECTED (asymmetric normalization: FTS folds vowels, cache folds nothing) | `answer_cache.py:117-124` vs `db.py:37` |
| Web-search path handles accent-less VN fine — upstream Perplexity does its own folding | **OBSERVED (working)** | trace `02_nodsc_legal.json` — correct NĐ168 sources |
| `DEEP_TERMS` diacritic VN entries never match unaccented typing | SUSPECTED | `depth_policy.py:52-62,80-88` |
| Abbreviations/teencode/misspellings: no expansion layer (`nđ`/`NĐ 168`, `tp hcm`, `ko`, `j`…) | SUSPECTED | only normalizer is `normalize_query` (casefold); store counts `tpho`→0 vs `tp hcm`→21 show the class of failure |
| Mixed EN-VN: handled only by the synth prompt ("same language"); no query rewrite for retrieval | OBSERVED-adequate | trace 05 — EN product names + VN grammar → coherent VN answer |
| Follow-up/intent inheritance: last-message-only by design | **OBSERVED** | trace 07 + `chat_completions.py:53-58`; outcome rescued only by NĐ168's topical salience |
| FTS5 `MATCH` gets raw user text → operator chars can alter semantics or throw | SUSPECTED | `store.py:62-63,274-278` |
| `vn` marker computed but never consumed | **OBSERVED** | `depth_policy.py:137-145` validates; `159-188` never reads `vn` |

---

## 5) Weakness candidates (discovery only — no fixes proposed)

Ranked by expected impact × evidence strength.

| # | Symptom | Layer | Evidence | Rough impact |
|---|---------|-------|----------|--------------|
| 1 | **Answer cache is dead on the live HTTP surface**: deep runs write draft+ledger and the gate passes offline on the same files, yet `packs` gains no row (`answers.db-wal` untouched since server boot 19:43 through both deep runs 20:31/20:33); identical repeat re-ran the full 30.7 s pipeline. Best-supported mechanism: `answer_cache.py:161-168` creates `sqlite3.connect(path)` **without `check_same_thread=False`**; `Engine._get_cache` builds it once (`engine.py:269-277`) but FastAPI serves sync handlers on rotating anyio worker threads → `sqlite3.ProgrammingError` on every `get`/`put`, swallowed into `warnings` (`engine.py:283-287,295-299`) that the wire drops anyway. `/readyz` still shows `cache:ok` because `status()` only checks `cache is not None` (`engine.py:490-491`). | cache | traces 05/08; packs=1 row (pre-existing); wal mtime; offline replay of the whole chain succeeds | every request pays full latency; every deep verify+publish is wasted work; cache-hit path unreachable |
| 2 | **MCP `hermes_search`/`hermes_extract` return `{"error":"no search backend available"}` on the live engine** — `getattr(engine,"backend",None)` (`tools.py:59-63`) can never hit `Engine._backend` (`engine.py:89`) | glue / fallback | **OBSERVED live** `tools/call`; `tools.py:118-123`; R8 acceptance exercised `hermes_vn` which doesn't need the backend | 2 of 6 MCP tools dead |
| 3 | Depth policy runs on degenerate signals: `extract_char_totals` never passed → `char_sum=0` → `+0.10` fires always, `−0.20` never; escalation driven only by markers+result-count; `vn` marker dead | intent (depth) | `engine.py:146-151`; `depth_policy.py:174-188` | every auto-depth query; near-threshold scores skewed by a constant |
| 4 | `đ` does not fold in FTS → accent-less VN misses `đ`-words on the store path | retrieval-recall / normalization | **OBSERVED** counts §4 (`dinh`6/`định`198, `duong`26/`đường`204, `da nang`3/`đà nẵng`80, `nghi dinh`0/`nghị định`25) | all unaccented `đ`-word queries on `hermes_store_query`/`hermes_vn`/`vn_news query` |
| 5 | HTTP response drops `sources`/`depth`/`reason`/`warnings`/`timings` — clients can't observe routing/health/cache state or failures like #1 (which is why #1 was invisible) | observability / citation | `chat_completions.py:72-87` | all API consumers; masked #1 |
| 6 | Trust scoring flat-lines on VN content: every source 0.20/`tier=unknown` (no VN hosts in the tier table) → ordering does nothing and every deep answer emits `low_trust` flags | source-selection / citation | **OBSERVED** reproduced `run_check` on real artifacts: `low_trust` on cited ids 1,2,4,6,7,8 | all VN-sourced answers (the product's core) |
| 7 | Follow-up context loss: only the last user message is queried | intent | `chat_completions.py:53-58`; trace 07 | multi-turn usage |
| 8 | Freshness blindness: `ttl_days=14` default (`research_pack.py:151`, `answer_cache.py:72`); price/news queries could serve 2-week-old packs. (Latent while #1 stands.) | freshness / cache | `answer_cache.py:376-384` | time-sensitive queries once cache works |
| 9 | Deep publish gate is mechanical-only (`judge="off"`, `engine.py:409`) — no semantic grounding check before caching; `quotes` are auto-fabricated from `content[:240]` (`engine.py:395`), not real evidence spans | citation / verification | `fact_check.py:655-671`; `engine.py:391-398` | whatever gets cached once #1 is fixed |
| 10 | Single serialized sidecar worker + per-op timeouts (search 60 s/extract 120 s): concurrent requests queue head-of-line; a slow extract stalls searches behind it | latency | `hermes_bridge.py:103,186-200`; one `Engine`/backend instance per app (`app.py:137`) | concurrent gateway traffic |
| 11 | Full extracts inlined into synth prompt (≤15 k chars × ≤8 sources → prompts up to ~120 k chars); no passage trimming/ranking | synthesis / latency | `synthesis.py:38-49`; `engine.py:321` | deep queries; long pages |
| 12 | `SourceRef.quote` = first 200 chars of extract — page position, not answer relevance | citation | `engine.py:213-222` | every cited answer (cosmetic) |
| 13 | FTS `MATCH` receives raw user text — operator chars (`"`,`*`,`OR`) alter semantics or error | retrieval-recall | `store.py:62-63,274-278` | MCP store/vn callers |
| 14 | Ambiguous queries get no clarification loop — engine searches literally and the synth hedges | intent | trace `06_ambiguous.json` (worked acceptably, but relied on synth hedging after irrelevant retrieval) | vague queries |

---

## Verify appendix

- `/healthz`: `2265.34 s` at start → `3495.01 s` at end; `/readyz` green throughout (`backend=hermes` worker alive; `cache.ok=true` — despite §5 #1, since the check only tests object presence).
- `git status --short` at file-write time showed `?? analysis/r9-system-map.md` as the only file added by this task (plus parallel agents' `analysis/r9-eval-inventory.md`, `evals/r9/`; a pre-existing `M analysis/scoreboard.md` was committed mid-session by the orchestrator, not by this task). `agent_logs/` is gitignored.
- Live call budget: 8 chat completions + 1 MCP session (initialize/initialized/`tools/call`×2) + read-only sqlite opens. No restarts, no file writes outside `analysis/r9-system-map.md` + `agent_logs/r9a_traces/`; the server's own `data/tmp/` artifacts and DB writes are its normal operation.
