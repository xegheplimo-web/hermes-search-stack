# search-prefetch

Speculative, keyless-only extract prefetch for the Hermes web pipeline (F2-c).

After a **successful** `web_search`, this plugin warms the **`web_extract` disk cache** for the
top results, so a later `web_extract` on one of those URLs skips its vendor network hop. It runs
on the `post_tool_call` seam in a daemon thread: the hook returns immediately and **never** raises
into the pipeline (fail-open everywhere).

## Honest expected gain

It saves **only the `web_extract` vendor fetch** — measured at ~**1.3 s p50 per URL** on this
machine (`analysis/EVIDENCE.md`). It does **not** remove the model round-trip: the model still has
to decide to call `web_extract`. It only pays off when the model subsequently extracts one of the
top-2 search URLs within the cache TTL (default 20 min). It is a latency shave, not a replacement
for the tool.

## How it works

| Stage | Behaviour |
|---|---|
| Trigger | `post_tool_call` where `tool_name == "web_search"` and the result JSON has `success: true`. |
| URLs | Top **≤2** `data.web[*].url`, in listed order, deduped. |
| Gate 1 — kill-switch | Skip if env `HERMES_SEARCH_PREFETCH=0` **or** plugin config `enabled: false`. |
| Gate 2 — keyless-only | Mirror the real extract dispatch chain (`tools/web_tools.py` `_get_extract_backend` → `plugins/web/keyless_mcp._ring_order`): explicit `web.extract_backend`/`web.backend` must name a keyless-ring vendor dispatching keyless; otherwise a stored shared selection → skip; otherwise **peek** `_ring_cursor` and take the first non-paid vendor. Cannot prove keyless → skip. |
| Gate 3 — already warm | Skip URLs already fresh in the extract disk cache (`extract_cache_get`). |
| Gate 4 — safety | Reuse the dispatcher's `_cacheable` gate plus http/https and the secret-URL refusal; skip localhost / private IP / exempt hosts. |
| Execution | Daemon thread; ≤2 URLs sequentially; `sleep(1.5)` before **each** vendor fetch (hard rule: live calls ≥1.5 s apart); single-flight per `(url, provider)`; global cap of **1** active prefetch worker (busy → skip). |
| Store | `extract_cache_put` with the **same key inputs the real dispatcher uses** (see below). |

### Provider selection (why not `get_active_extract_provider`)

On a managed install, extract dispatch does **not** use
`agent.web_search_registry.get_active_extract_provider()`: that resolves the managed Perplexity
route, which is **search-only** ("the extract ladder is untouched"). Real dispatch runs
`_get_extract_backend()` → `_autodetect_backend() or _keyless_backend()`, and with no keyed
backend the **keyless ring** serves it: the serving vendor is the first non-paid vendor in
`ring[cursor:]`, and the cursor advances **once per request** (`keyless_mcp._ring_order`). The
plugin therefore **peeks** `_ring_cursor` (never calls `_ring_order` — that would consume the next
request's slot) and fetches through the ring's own `<vendor>_extract_keyless` function, so the
cache key it writes is exactly the `(url, format, provider)` key the next `web_extract` will read
with. Live proof (2026-10-06): prefetch stored under `parallel`; the next `web_extract` resolved
`parallel` and served `web_extract cache hit` in **0.09 s** (vs ~1.25 s live fetch).

### Cache-key parity

`tools/web_tools_extract.py` writes:

```python
extract_cache_put(url, _content, title, format=format, provider=provider.name)
```

where `_content = raw_content or content` and `format` is the model's requested format (default
`None`). `tools/web_result_cache._url_digest` normalizes `format or "markdown"`, so `None` and
`"markdown"` collide on the **same** key by design. This plugin therefore calls
`extract_cache_put(url, content, title, format="markdown", provider=provider.name)` and reads with
`extract_cache_get(url, format="markdown", provider=provider.name)` — matching the common
default-format dispatch. A later `web_extract` with an explicit non-markdown `format` uses a
different key and will miss (that fetch is then paid as usual).

`extract_cache_put` itself writes both the dedicated `<host>-<digest>.cache.md` file and the JSON
index, so **no separate full-text store mirror is needed** — this plugin writes exactly what the
dispatcher writes.

## Enabling (the orchestrator runs these; this repo edits no config)

The plugin is a **user plugin**: a flat directory under `$HERMES_HOME/plugins/<name>/` (one
directory per plugin — the same layout `hermes plugins install` produces; `$HERMES_HOME` defaults
to `C:\Users\<you>\AppData\Local\hermes` on Windows). Its registry key is the directory name:
**`search-prefetch`**.

1. Copy this directory to the profile:

   ```powershell
   Copy-Item -Recurse plugins\search-prefetch "$env:HERMES_HOME\plugins\search-prefetch"
   ```

   (If `HERMES_HOME` is unset, use `C:\Users\<you>\AppData\Local\hermes\plugins\search-prefetch`.)

2. Enable it. Plugins are **opt-in**: the plugin only registers its hook once its key is in the
   `plugins.enabled` allow-list in `$HERMES_HOME/config.yaml`:

   ```yaml
   plugins:
     enabled:
       - search-prefetch
   ```

   Equivalently, run `hermes plugins enable search-prefetch`. (A key in `plugins.disabled` wins
   over `plugins.enabled`.)

3. Restart Hermes (plugins are loaded at startup; use `/reload` if your build supports it).

4. Verify with `hermes plugins list` — `search-prefetch` should show as enabled with hook
   `post_tool_call`.

## Disabling / kill-switch

Any one of these stops prefetch:

- `hermes plugins disable search-prefetch` (adds `search-prefetch` to `plugins.disabled`), or
  remove it from `plugins.enabled`;
- set the plugin config `enabled: false` — `plugins.entries.search-prefetch.settings.enabled`
  in `$HERMES_HOME/config.yaml` (this is the config the plugin reads via `ctx.get_config("enabled")`;
  the frozen contract's shorthand `plugins.search_prefetch.enabled` maps to this real path);
- **env kill-switch** `HERMES_SEARCH_PREFETCH=0` (per-process; overrides the config).

## Log

Append-only, one line per URL, at `$HERMES_HOME/logs/search-prefetch.log`
(override the path with `HERMES_SEARCH_PREFETCH_LOG`):

```
ISO_TS | url | provider | STORED
ISO_TS | url | provider | SKIP:<reason>
ISO_TS | url | provider | ERR:<msg>
```

`<reason>` is one of `disabled`, `busy`, `provider-not-keyless`, `unsafe-url`, `cache-warm`,
`in-flight`, `empty`. Example:

```
2026-10-06T14:03:11 | https://example.com/a | exa | STORED
2026-10-06T14:03:11 | https://example.com/b |  | SKIP:cache-warm
```

## Compliance

Keyless-only, ≤2 URLs, paced ≥1.5 s, single-flight, one worker — no `web.search_backend` /
`web.backend` pinning, no `ddgs` install, no `provider_tier.firecrawl` change, and no core-file
edits. The plugin loads without the Hermes runtime (all Hermes imports are lazy).
