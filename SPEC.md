# Project: Hermes Search Stack — "Search như Perplexity.ai"

**Orchestrator:** Hermes (Lead) · **Date:** 2026-10-06
**Goal:** Đảm bảo `web_search` + `web_extract` của Hermes đạt chất lượng "như Perplexity.ai" — đã cấu hình, đã kiểm chứng, có fallback, có bằng chứng.

## Current state (audited by orchestrator)
- Hermes v0.21.5+7337, native Windows install.
- **Search routing:** `web.search_backend` để TRỐNG (auto) → resolve ra **free managed Perplexity (search_type=fast) qua Nous Tool Gateway identity** — đã xác nhận bằng log `Perplexity search: ... (managed)`.
- **⚠️ CRITICAL:** KHÔNG được set `web.search_backend` / `web.backend` (dù là "perplexity") — code `_managed_web_search()` trả False khi key này được set, làm MẤT managed route (không có PERPLEXITY_API_KEY trực tiếp) → tụt xuống keyless. Giữ nguyên trạng thái auto.
- **Extract routing:** keyless chain (Exa/Parallel/Firecrawl/Keenable free tiers — `plugins/web/keyless_mcp.py`), rescue bật (`keyless_rescue: true`).
- End-to-end verified: web_search live OK (Vietnamese query trả kết quả), web_extract 3 URL OK.

## Environment (verified)
- HERMES_HOME: `C:/Users/atton/AppData/Local/hermes`
- Source: `C:/Users/atton/AppData/Local/hermes/hermes-agent`
- Venv python: `C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe` (Python 3.14.7)
- Run recipe (copy-paste, verified working):
```bash
PYTHONPATH="C:/Users/atton/AppData/Local/hermes/hermes-agent" \
HERMES_HOME="C:/Users/atton/AppData/Local/hermes" \
"C:/Users/atton/AppData/Local/hermes/installs/6e6d900815cdde0a/environments/6443484ef2bf47a8a070762d2318e3b2/venv/Scripts/python.exe" <script.py>
```
- Entry points (verified by PoC):
  - `tools.web_tools.web_search_tool(query: str, limit: int = 5) -> str` — **SYNC**, returns JSON string `{"success":true,"data":{"web":[{title,url,description,position}]}}`.
  - `tools.web_tools.web_extract_tool(urls: list, format: str = None, char_limit: int | None = None) -> str` — **ASYNC** (wrap with `asyncio.run`), returns JSON string with `results[]` each `{url,title,content,error?}`.
  - Resolution helpers: `tools.web_tools._managed_web_search()` (expect True), `tools.web_tools._get_search_backend()` (expect "perplexity").
- Backend evidence per call: attach a `logging` handler (level INFO) to root logger before calls; captured lines contain markers like `Web search via perplexity`, `Web extract via parallel`, `Parallel keyless extract`, `(managed)`.

## Rules (both tasks)
- Create/modify files ONLY inside `C:/Users/atton/hermes-search-stack/`.
- NEVER modify `config.yaml`, `.env`, `auth.json` or anything under HERMES_HOME. Never print secret values.
- No package installs. Stdlib + existing venv packages only.
- Politeness: `time.sleep(1.5–2)` between live network calls; keep total live calls per script ≤ 30.
- Deterministic queries (fixed lists below). No paid API keys.

## Task T1 — Cline: `verify_web_stack.py`
Build + run the search/extract quality battery (details in launch prompt). Pass/fail per case, JSON + MD outputs into `results/`.

## Task T2 — OpenCode: `test_keyless_fallback.py`
Prove keyless fallback vendors (ddgs + keyless_mcp: Exa/Parallel/Firecrawl/Keenable) work independently of the managed route (details in launch prompt). Outputs into `results/`.

## Escalation — Devin (standby)
Escalate to Devin ONLY IF: (a) a task fails its acceptance criteria after ONE fix/reassignment cycle by the original agent, or (b) orchestrator's final verification finds a hard failure (backend resolution bug, fallback broken, extract chain broken). Devin gets the exact failing case + error log + scope.

## Orchestrator final verification protocol
1. Re-run T1 + T2 scripts cold (same recipe) → results must be reproducible.
2. `web.backend`, `web.search_backend`, `web.extract_backend` still empty in config.yaml (no regression).
3. E2E: fresh `hermes chat -q` session answers a Vietnamese + a news query WITH real dated sources.
4. Compile REPORT.md with evidence (PASS/FAIL table, backends used, latencies).

## Change log
- 2026-10-06 02:45 — **ddgs experiment REVERTED**: installing `ddgs` (via `hermes tools post-setup ddgs`) hijacked search autodetect (`Web search via ddgs`) and broke web_extract ("ddgs is a search-only backend"). Reverted: `facts.json` extras → `["all"]` + `hermes pm repair`. Post-revert verified: search = managed Perplexity, extract = parallel keyless.
- Active venv path CHANGES when Hermes repairs/rebuilds environments — always resolve the current one with `hermes doctor | grep "Runtime venv"`. Last verified: `.../environments/c31a367ff6674cb9b9a80fcf6e6c96a0/venv`.

## Deliverables
- `verify_web_stack.py`, `test_keyless_fallback.py` + `results/*.json`, `results/*.md`
- `REPORT.md` (orchestrator)
- Maintenance notes recorded in a Hermes skill.
