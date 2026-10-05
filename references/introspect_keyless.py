"""Introspection dump for T2 (OpenCode): API surface of keyless fallback modules + 1 live sanity call each."""

import inspect
import json
import traceback


def sig(fn):
    try:
        return f"{fn.__name__}{inspect.signature(fn)}"
    except Exception as e:
        return f"{fn.__name__} <? {e}>"


def doc1(fn):
    d = (inspect.getdoc(fn) or "").strip().split("\n")[0]
    return d[:180]


out = []
try:
    import plugins.web.keyless_mcp as km

    out.append("## modules.web.keyless_mcp")
    out.append(f"keyless_enabled() -> {km.keyless_enabled()!r}" if callable(km.keyless_enabled) else "")
    for name in [
        "keyless_enabled",
        "parallel_search_keyless",
        "parallel_extract_keyless",
        "exa_search_keyless",
        "exa_extract_keyless",
        "firecrawl_search_keyless",
        "firecrawl_extract_keyless",
        "keenable_search_keyless",
        "keenable_extract_keyless",
        "search_with_failover",
        "extract_with_failover",
        "mcp_call",
        "use_keyless",
        "provider_tier",
        "_ring_order",
    ]:
        fn = getattr(km, name, None)
        if fn is None:
            out.append(f"- {name}: MISSING")
        else:
            out.append(f"- {sig(fn)}  # {doc1(fn)}")
except Exception:
    out.append("keyless_mcp import failed:\n" + traceback.format_exc())

out.append("")
try:
    from plugins.web.ddgs.provider import DDGSWebSearchProvider

    out.append("## plugins.web.ddgs.provider")
    out.append(f"- class DDGSWebSearchProvider: __init__{inspect.signature(DDGSWebSearchProvider.__init__)}")
    out.append(f"- search{sig(DDGSWebSearchProvider.search)}")
    out.append(f"- is_available{sig(DDGSWebSearchProvider.is_available)}")
except Exception:
    out.append("ddgs import failed:\n" + traceback.format_exc())

out.append("")
out.append("## live sanity calls (1 each)")
try:
    p = DDGSWebSearchProvider()
    r = p.search("open source ai agent framework 2026", limit=2)
    out.append("DDGS.search -> " + json.dumps(r, ensure_ascii=False)[:400])
except Exception:
    out.append("DDGS live call failed:\n" + traceback.format_exc())

try:
    r = km.parallel_search_keyless("best laptop 2026", 2)
    out.append("parallel_search_keyless -> " + json.dumps(r, ensure_ascii=False)[:400])
except Exception:
    out.append("parallel keyless live call failed:\n" + traceback.format_exc())

try:
    r = km.extract_with_failover("parallel", ["https://example.com"])
    out.append("extract_with_failover(parallel) -> " + json.dumps(r, ensure_ascii=False)[:400])
except Exception:
    out.append("extract failover live call failed:\n" + traceback.format_exc())

print("\n".join(out))
