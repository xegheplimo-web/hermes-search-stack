"""search-prefetch — speculative keyless extract prefetch on the ``post_tool_call`` seam.

After a *successful* ``web_search``, warm the **extract disk cache** for the top results so a
later ``web_extract`` on those URLs skips its vendor network hop. The work runs in a daemon
thread; the hook returns immediately and never raises into the pipeline (fail-open everywhere).

What it saves (and what it does not)
------------------------------------
It saves **only** the ``web_extract`` vendor fetch (~1.3 s p50 per URL on this machine —
``analysis/EVIDENCE.md``). It does **not** remove the model round-trip: the model still has to
decide to call ``web_extract``. Prefetch is a latency shave, not a replacement for the tool, and
it only pays off when the model subsequently extracts one of the top-2 search URLs.

Gates (any failure → one ``SKIP:<reason>`` log line; see README)
---------------------------------------------------------------
1. kill-switch: env ``HERMES_SEARCH_PREFETCH=0`` OR plugin config ``enabled: false``.
2. keyless-only: the vendor the NEXT ``web_extract`` dispatch will use must be a keyless-ring
   vendor dispatching keyless (mirrors the real dispatch chain, ``tools/web_tools.py``
   ``_get_extract_backend`` → ``plugins/web/keyless_mcp._ring_order``; see "Provider selection"
   below). Cannot prove keyless → skip.
3. cache-already-warm: skip URLs already fresh in the extract disk cache (``extract_cache_get``).
4. safety: reuse the dispatcher's ``_cacheable`` gate (+ http/https + secret-URL refusal); skip
   localhost / private IP / blocked hosts.

Execution: daemon thread, ≤2 URLs processed sequentially, ``sleep(1.5)`` before **each** vendor
fetch, single-flight per ``(url, provider)``, and a global cap of **1** active prefetch worker.

Provider selection (why not ``get_active_extract_provider``)
------------------------------------------------------------
On a managed install, extract dispatch does **not** use
``agent.web_search_registry.get_active_extract_provider()``: that resolves the managed Perplexity
route, which is **search-only** ("the extract ladder is untouched" — ``tools/web_tools.py``
``_managed_web_search``). Real dispatch runs ``_get_extract_backend()`` →
``_autodetect_backend() or _keyless_backend()``, and with no keyed backend the keyless ring
serves it: the serving vendor is ``first non-paid vendor in ring[cursor:]`` and the cursor
advances **once per request** (``keyless_mcp._ring_order``). This plugin therefore **peeks**
``_ring_cursor`` (never calls ``_ring_order`` — that would consume the next request's slot) and
fetches through the ring's own ``<vendor>_extract_keyless`` function, so the cache key it writes
is exactly the ``(url, format, provider)`` key the next ``web_extract`` will read with. A
2026-10-06 live smoke of the previous design (registry-based) skipped every URL
(``SKIP:provider-not-keyless``) because the registry route never serves keyless extract.

Cache-key parity (mirrored from the real dispatcher)
----------------------------------------------------
``tools/web_tools_extract.py`` caches a successful fetch with::

    extract_cache_put(url, _content, title, format=format, provider=provider.name)

where ``_content = raw_content or content`` and ``format`` is the model's requested format
(default ``None``). ``tools/web_result_cache._url_digest`` normalizes ``format or "markdown"``, so
``None`` and ``"markdown"`` collide on the **same** key by design. This plugin therefore calls
``extract_cache_put(url, content, title, format="markdown", provider=provider.name)`` and reads
with ``extract_cache_get(url, format="markdown", provider=provider.name)`` — matching the common
default-format dispatch. A later ``web_extract`` with an explicit non-markdown ``format`` uses a
different key and will miss (documented; that fetch is then paid as usual).

No separate full-text store mirror is needed: ``extract_cache_put`` itself writes the dedicated
``<host>-<digest>.cache.md`` file **and** the JSON index, so this plugin writes exactly what the
dispatcher writes.

Import safety: this module imports **only** the standard library at top level; every Hermes import
is lazy (inside a function), so it loads with no Hermes runtime on ``sys.path``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

_PLUGIN_KEY = "search-prefetch"
_LOG_FILENAME = "search-prefetch.log"
# Mirrors the dispatcher's default: extract_cache_* normalize ``format or "markdown"``.
_EXTRACT_FORMAT = "markdown"
_MAX_URLS = 2
# Hard rule: any two live calls must be ≥1.5 s apart. We pace before every vendor fetch.
_PACING_SECONDS = 1.5
_SAFE_SCHEMES = ("http", "https")

# Set by register(); used only to read plugin-relative config in the gate.
_CTX: Any = None
# Global cap: exactly one active prefetch worker. Acquired by the hook, released by the worker.
_worker_lock = threading.Lock()
# Single-flight: (url, provider) pairs currently being prefetched.
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


def register(ctx: Any) -> None:
    """Register the ``post_tool_call`` observer. Called by the Hermes plugin loader."""
    global _CTX
    _CTX = ctx
    ctx.register_hook("post_tool_call", _on_post_tool_call)


# ─── Hook ──────────────────────────────────────────────────────────────────────


def _on_post_tool_call(tool_name: str = "", result: Any = None, **_ignored: Any) -> None:
    """``post_tool_call`` observer: prefetch the top search-result URLs. Never raises.

    Only ``web_search`` successes are considered. The cheap gates (success parse, kill-switch,
    single-worker slot) run inline; the worker thread does the provider resolution, per-URL
    safety/cache gates, pacing, fetch and cache write.
    """
    try:
        if tool_name != "web_search":
            return
        data = _parse_result(result)
        if not isinstance(data, dict) or not data.get("success"):
            return
        urls = _extract_urls(data)
        if not urls:
            return
        if not _enabled():
            for url in urls:
                _log(url, "", "SKIP:disabled")
            return
        if not _worker_lock.acquire(blocking=False):
            for url in urls:
                _log(url, "", "SKIP:busy")
            return
        try:
            threading.Thread(target=_spawn, args=(urls,), name="search-prefetch", daemon=True).start()
        except Exception:
            _worker_lock.release()
            raise
    except Exception:
        # Fail-open: a prefetch hook must never break a turn.
        return


def _spawn(urls: list[str]) -> None:
    """Thread body: run the worker, then always release the single-worker slot."""
    try:
        _prefetch_worker(urls)
    except Exception as exc:  # noqa: BLE001 — best-effort background work
        _log("", "", f"ERR:{exc}")
    finally:
        _worker_lock.release()


# ─── Worker ────────────────────────────────────────────────────────────────────


def _prefetch_worker(urls: list[str]) -> None:
    """Resolve the next keyless extract vendor, then fetch + store up to two URLs sequentially."""
    try:
        vendor = _next_extract_vendor()
    except Exception as exc:  # noqa: BLE001 — defense in depth: never raise out of the worker
        _log("", "", f"ERR:{exc}")
        return
    if vendor is None:
        for url in urls:
            _log(url, "", "SKIP:provider-not-keyless")
        return
    for url in urls:
        _prefetch_one(vendor, url)


def _prefetch_one(vendor: str, url: str) -> None:
    """Gate, fetch and cache one URL; log exactly one outcome line. Never raises."""
    try:
        if not _is_safe_public_url(url):
            _log(url, vendor, "SKIP:unsafe-url")
            return
        if _is_cache_warm(url, vendor):
            _log(url, vendor, "SKIP:cache-warm")
            return
        if not _claim_inflight(url, vendor):
            _log(url, vendor, "SKIP:in-flight")
            return
        try:
            time.sleep(_PACING_SECONDS)  # pace before the vendor fetch
            entry = _fetch_one(vendor, url)
            if entry is None:
                _log(url, vendor, "ERR:no-result")
                return
            if entry.get("error"):
                _log(url, vendor, f"ERR:{entry['error']}")
                return
            content = entry.get("raw_content") or entry.get("content") or ""
            if not content:
                _log(url, vendor, "SKIP:empty")
                return
            _cache_put(url, content, entry.get("title", "") or "", vendor)
            _log(url, vendor, "STORED")
        finally:
            _release_inflight(url, vendor)
    except Exception as exc:  # noqa: BLE001 — one bad URL must not stop the batch
        _log(url, vendor, f"ERR:{exc}")


# ─── Result parsing / URL extraction ───────────────────────────────────────────


def _parse_result(result: Any) -> Any:
    """Best-effort parse of the tool result (``web_search_tool`` returns a JSON string)."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except Exception:  # noqa: BLE001 — non-JSON result is simply not prefetchable
            return None
    return None


def _extract_urls(data: dict) -> list[str]:
    """Top ≤2 ``data.web[*].url`` in listed order, deduped; non-empty strings only."""
    try:
        web = data.get("data", {}).get("web")
    except Exception:  # noqa: BLE001 — unexpected shape is not prefetchable
        return []
    if not isinstance(web, list):
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for item in web:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str):
            continue
        url = url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= _MAX_URLS:
            break
    return urls


# ─── Gates ─────────────────────────────────────────────────────────────────────


def _enabled() -> bool:
    """Kill-switch (env) OR config gate. Default enabled."""
    if os.environ.get("HERMES_SEARCH_PREFETCH", "").strip() == "0":
        return False
    return _config_enabled()


def _config_enabled() -> bool:
    """Plugin config gate; default ``True``.

    A plugin reads its own settings via ``ctx.get_config("enabled")`` — i.e. the real config path
    ``plugins.entries.<plugin-key>.settings.enabled`` (legacy ``.config.enabled``). The frozen
    contract's shorthand ``plugins.search_prefetch.enabled`` maps to that real path; the registry
    key for a flat user plugin is the directory name ``search-prefetch``. Any read failure defaults
    to enabled (the env kill-switch still wins).
    """
    try:
        if _CTX is not None:
            return _coerce_bool(_CTX.get_config("enabled", True), True)
    except Exception:  # noqa: BLE001 — config problems never break the hook
        pass
    try:
        from hermes_cli.config import load_config_readonly

        plugins = (load_config_readonly() or {}).get("plugins")
        entry = (plugins.get("entries") or {}).get(_PLUGIN_KEY) if isinstance(plugins, dict) else None
        if isinstance(entry, dict):
            for subtree in ("settings", "config"):
                node = entry.get(subtree)
                if isinstance(node, dict) and "enabled" in node:
                    return _coerce_bool(node["enabled"], True)
    except Exception:  # noqa: BLE001 — config layer optional in stripped envs
        pass
    return True


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ("false", "0", "no", "off"):
            return False
        if token in ("true", "1", "yes", "on"):
            return True
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _next_extract_vendor() -> str | None:
    """Vendor name the next keyless ``web_extract`` dispatch will read/write cache keys with.

    Mirrors the real dispatch chain (``tools/web_tools.py`` ``_get_extract_backend`` →
    ``agent/web_search_registry._keyless_preference`` → ``plugins/web/keyless_mcp._ring_order``):
    an explicit ``web.extract_backend``/``web.backend`` wins; otherwise, when no stored shared
    selection exists, unpinned extract traffic round-robins the keyless ring, serving the first
    non-paid vendor in ``ring[cursor:]`` (the cursor advances once per request — this function only
    PEEKS it, so it never consumes the slot the next real request will use).

    Returns ``None`` when the next dispatch cannot be proven keyless (explicit non-ring backend,
    stored selection, all ring vendors pinned paid, or any read failure) — prefetch then skips.
    """
    try:
        from hermes_cli.config import load_config_readonly

        web = (load_config_readonly() or {}).get("web") or {}
        configured = str(web.get("extract_backend") or web.get("backend") or "").strip().lower()
    except Exception:  # noqa: BLE001 — config layer optional
        configured = ""
    if configured:
        return configured if _keyless_dispatch_name(configured) else None
    try:
        from tools.tool_backend_helpers import read_selection

        if read_selection("web") is not None:
            return None  # a stored selection routes extract away from the keyless ring
    except Exception:  # noqa: BLE001 — helper optional in stripped envs; fall through to the peek
        pass
    return _peek_ring_vendor()


def _keyless_dispatch_name(name: str) -> bool:
    """True when an explicitly configured backend *name* dispatches keyless (ring member + use_keyless)."""
    try:
        from plugins.web.keyless_mcp import _KEYLESS_RING, use_keyless

        if name not in _KEYLESS_RING:
            return False
        return bool(use_keyless(name, _provider_key(name)))
    except Exception:  # noqa: BLE001 — cannot prove keyless → not keyless
        return False


def _peek_ring_vendor() -> str | None:
    """First non-paid vendor in ``ring[cursor:]`` — the slot the next keyless request serves.

    Peek only: reading ``_ring_cursor`` must never advance it, or the prefetched cache key would
    belong to the vendor *after* the one the next ``web_extract`` uses.
    """
    try:
        from plugins.web.keyless_mcp import _KEYLESS_RING, _ring_cursor, provider_tier

        ring = tuple(_KEYLESS_RING)
        if not ring:
            return None
        start = int(_ring_cursor) % len(ring)
        for vendor in ring[start:] + ring[:start]:
            if provider_tier(vendor) != "paid":
                return vendor
        return None
    except Exception:  # noqa: BLE001 — ring optional in stripped envs
        return None


def _provider_key(name: str) -> str:
    """The vendor API key value (empty when unset) that feeds the keyless decision.

    May raise (e.g. an unscoped-secret error); the caller treats that as "cannot prove keyless".
    """
    key_env = ""
    try:
        import agent.web_search_registry as registry_mod

        get_provider = getattr(registry_mod, "get_provider", None)
        provider = get_provider(name) if get_provider is not None else None
        key_env = getattr(provider, "KEY_ENV", "") or ""
    except Exception:  # noqa: BLE001 — provider optional; fall back to the conventional env name
        pass
    key_env = key_env or f"{name.upper()}_API_KEY"
    try:
        from agent.web_search_provider import get_provider_env
    except Exception:  # noqa: BLE001 — stripped env: fall back to os.environ
        return os.environ.get(key_env, "").strip()
    return get_provider_env(key_env)


def _is_safe_public_url(url: str) -> bool:
    """Dispatcher-aligned safety gate: http/https public hosts only, no secret-bearing URLs.

    Prefers the real cache module's ``_cacheable`` gate (cache-enabled + not local-dev/exempt) and
    the secret-URL check when importable; otherwise falls back to a conservative host test.
    """
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001 — unparseable → unsafe
        return False
    if parsed.scheme not in _SAFE_SCHEMES or not parsed.hostname:
        return False
    try:
        from tools.web_result_cache import _cacheable

        if not _cacheable(url):
            return False
    except Exception:  # noqa: BLE001 — cache module unavailable: conservative host test
        if not _conservative_host_ok(parsed.hostname):
            return False
    return not _has_secret(url)


def _conservative_host_ok(hostname: str) -> bool:
    """Fallback host test: reject loopback/private/LAN names when ``_cacheable`` is unavailable."""
    host = hostname.strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    if "." not in host and ":" not in host:  # single-label LAN name, not public DNS
        return False
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # public DNS name
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified)


def _has_secret(url: str) -> bool:
    """True when the URL carries what looks like an API key/token (dispatcher refuses these)."""
    try:
        from agent.redact import _PREFIX_RE

        return any(_PREFIX_RE.search(candidate) for candidate in (url, unquote(url)))
    except Exception:  # noqa: BLE001 — redactor unavailable: cannot flag
        return False


def _is_cache_warm(url: str, provider_name: str) -> bool:
    """True when a fresh extract-cache entry already exists for this key."""
    try:
        from tools.web_result_cache import extract_cache_get

        return extract_cache_get(url, format=_EXTRACT_FORMAT, provider=provider_name) is not None
    except Exception:  # noqa: BLE001 — cache read failure → treat as cold (best-effort)
        return False


# ─── Fetch / store ─────────────────────────────────────────────────────────────


def _fetch_one(vendor: str, url: str) -> dict | None:
    """Fetch one URL via the keyless ring's own extractor for *vendor* (no cursor interaction).

    Calls ``plugins.web.keyless_mcp.<vendor>_extract_keyless`` directly — the same function the
    real dispatch reaches through the vendor's provider — instead of ``provider.extract()``, which
    would advance the ring cursor and steal the next request's slot. The returned entry is matched
    to *url* exactly like the dispatcher does (``url`` or ``metadata.sourceURL``).
    """
    from plugins.web.keyless_mcp import _KEYLESS_EXTRACTORS

    extract = _KEYLESS_EXTRACTORS.get(vendor)
    if extract is None:
        return None
    result = extract([url])
    if inspect.iscoroutine(result):
        result = asyncio.run(result)
    if not isinstance(result, list) or not result:
        return None
    for entry in result:
        if not isinstance(entry, dict):
            continue
        meta = entry.get("metadata")
        source = meta.get("sourceURL") if isinstance(meta, dict) else None
        if url in (entry.get("url"), source):
            return entry
    first = result[0]
    return first if isinstance(first, dict) else None


def _cache_put(url: str, content: str, title: str, provider_name: str) -> None:
    """Mirror ``tools/web_tools_extract.py``'s cache write exactly (see module docstring)."""
    from tools.web_result_cache import extract_cache_put

    extract_cache_put(url, content, title, format=_EXTRACT_FORMAT, provider=provider_name)


# ─── Single-flight ─────────────────────────────────────────────────────────────


def _claim_inflight(url: str, provider_name: str) -> bool:
    """Atomically claim ``(url, provider)``; False when another prefetch already owns it."""
    key = f"{url}\n{provider_name}"
    with _inflight_lock:
        if key in _inflight:
            return False
        _inflight.add(key)
        return True


def _release_inflight(url: str, provider_name: str) -> None:
    with _inflight_lock:
        _inflight.discard(f"{url}\n{provider_name}")


# ─── Logging ───────────────────────────────────────────────────────────────────


def _log(url: str, provider: str, status: str) -> None:
    """Append ``ISO_TS | url | provider | STORED|SKIP:<reason>|ERR:<msg>``. Best-effort."""
    try:
        stamp = datetime.now().isoformat(timespec="seconds")
        line = f"{stamp} | {_flat(url)} | {_flat(provider)} | {_flat(status)}\n"
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:  # noqa: BLE001 — logging must never break the hook
        return


def _flat(text: Any) -> str:
    """Collapse newlines so every outcome stays on one log line."""
    return str(text).replace("\r", " ").replace("\n", " ")


def _log_path() -> Path:
    override = os.environ.get("HERMES_SEARCH_PREFETCH_LOG", "").strip()
    if override:
        return Path(override)
    return _hermes_home() / "logs" / _LOG_FILENAME


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 — stripped env: env var or platform default
        env = os.environ.get("HERMES_HOME", "").strip()
        if env:
            return Path(env)
        return Path.home() / ".hermes"
