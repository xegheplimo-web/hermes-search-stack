"""Gateway engine — cache -> probe -> needs_depth -> fast/deep flows (r8 §5).

``Engine.run_iter`` emits ``{"type":"route"}`` -> ``{"type":"delta"}`` ->
``{"type":"done"}`` events; ``run`` consumes them into an ``EngineResult``.
Deep answers are published to the ``AnswerCache`` only after the
``fact_check`` gate passes (``research_pack.build_pack`` + ``put``). Every
failure degrades into ``warnings`` — the engine never crashes on backend,
synthesis, or verification errors. Repo modules (``depth_policy``, ``trust``,
``fact_check``, ``research_pack``, ``searchstore``) are imported lazily so the
gateway degrades gracefully outside the repo context.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from gateway.core.router import build_signals, decide, query_markers, split_query
from gateway.protocols import EvidenceItem, ExtractItem, SearchItem
from gateway.security.admission import ADMISSION_WAIT_MS

if TYPE_CHECKING:
    from gateway.config import GatewayConfig
    from gateway.protocols import SearchBackend

_DEPTHS = ("auto", "fast", "deep")


def _ms() -> float:
    return time.perf_counter() * 1000.0


def _normalize_url(url: str) -> str:
    """Mirror ``research_pack._normalize_url``: strip fragment + trailing '/'."""
    u = (url or "").strip()
    if "#" in u:
        u = u.split("#", 1)[0]
    stripped = u.rstrip("/")
    return stripped or u


def _request_deadline_s(config) -> float:
    """Per-request deadline in seconds; ``<= 0`` disables the watchdog."""
    try:
        return float(getattr(config, "request_deadline_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _admission_wait_ms() -> float:
    """Queue wait recorded by the admission middleware; 0 outside the app."""
    try:
        return max(0.0, float(ADMISSION_WAIT_MS.get(0.0) or 0.0))
    except Exception:  # noqa: BLE001 — a broken context must not kill the query
        return 0.0


@dataclass(slots=True)
class SourceRef:
    """One cited source in an ``EngineResult`` (id = ``[n]`` citation number)."""

    id: int
    title: str
    url: str
    quote: str = ""
    trust_score: float | None = None


@dataclass(slots=True)
class EngineResult:
    """Frozen result shape (r8-interfaces.md §5)."""

    answer_markdown: str
    sources: list[SourceRef]
    depth: str  # "cache" | "fast" | "deep"
    cached: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)


def _search_items_to_evidence(items: list[SearchItem]) -> list[EvidenceItem]:
    """Snippet-only evidence fallback when extraction fails wholesale."""
    return [
        EvidenceItem(id=i + 1, title=it.title, url=it.url, content=it.description or "") for i, it in enumerate(items)
    ]


class Engine:
    """Synchronous query engine: cache -> probe -> fast/deep -> synthesize."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        backend: SearchBackend | None = None,
        synth=None,
        cache=None,
        metrics=None,
    ):
        self.config = config
        self._backend = backend
        self._synth = synth
        self._cache = cache
        self._cache_failed = False
        self._cache_error: str | None = None
        # Optional GatewayMetrics sink (additive); deadline hits count as timeouts.
        self.metrics = metrics

    @property
    def backend(self) -> SearchBackend:
        """Public read-only accessor for the active search backend (r9 §B).

        Lazily resolved via :meth:`_get_backend` — first access builds it
        from ``config.backend``. MCP tools reach it through the public
        attribute (``getattr(engine, "backend", None)``); inject
        ``backend=`` at construction to override.
        """
        return self._get_backend()

    # ---------- frozen API ----------

    def run(self, query: str, *, depth: str = "auto", allow_cache: bool = True) -> EngineResult:
        """Consume ``run_iter`` and return the final ``EngineResult``."""
        final: EngineResult | None = None
        for event in self.run_iter(query, depth=depth, allow_cache=allow_cache):
            if event.get("type") == "done":
                final = event["result"]
        if final is None:  # defensive: an event-stream bug must not propagate
            return EngineResult(
                answer_markdown="",
                sources=[],
                depth="error",
                cached=False,
                warnings=["engine produced no result event"],
            )
        return final

    def run_iter(self, query: str, *, depth: str = "auto", allow_cache: bool = True) -> Iterator[dict]:
        """Event stream: ``route`` -> ``delta``* -> ``done`` (frozen §5)."""
        warnings: list[str] = []
        timings: dict[str, int] = {}
        t0 = _ms()
        query = str(query or "").strip()

        # R15-D watchdog: the admission queue wait counts against the request
        # deadline (it is part of the caller-visible latency). ``<= 0``
        # disables the check entirely. The deadline is cooperative — it is
        # tested between stages, skips optional work, and never raises.
        admission_wait = _admission_wait_ms()
        timings["admission_wait_ms"] = int(admission_wait)
        timings["deadline_exceeded"] = 0
        deadline_s = _request_deadline_s(self.config)
        deadline_at = (t0 - admission_wait) + deadline_s * 1000.0 if deadline_s > 0 else None
        deadline_hit = False

        def deadline_passed() -> bool:
            nonlocal deadline_hit
            if deadline_hit:
                return True
            if deadline_at is None or _ms() <= deadline_at:
                return False
            deadline_hit = True
            warnings.append(f"request deadline exceeded ({deadline_s:g}s) — partial result")
            self._note_timeout()
            return True

        # 1) cache-first: a fresh verified pack serves with no live calls.
        if allow_cache:
            t = _ms()
            hit = self._cache_get(query, warnings)
            timings["cache_ms"] = int(_ms() - t)
            if hit and hit.get("fresh"):
                result = self._result_from_pack(hit, warnings, timings, t0)
                yield {"type": "route", "depth": "cache", "cached": True, "reason": result.reason}
                if result.answer_markdown:
                    yield {"type": "delta", "text": result.answer_markdown}
                yield {"type": "done", "result": result}
                return
            if hit:
                warnings.append(f"stale cache entry (age {hit.get('age_days', 0):.1f}d > ttl) — refreshing")

        # 2) probe search (one query) + depth decision.
        errors: list[str] = []
        t = _ms()
        probe = self._safe_search(query, self.config.fast_max_results, errors, warnings)
        timings["probe_ms"] = int(_ms() - t)
        markers = query_markers(query)

        if depth in ("fast", "deep"):
            mode, reason = depth, f"forced depth={depth}"
        else:
            if depth != "auto":
                warnings.append(f"unknown depth {depth!r} — using auto")
            # extract_char_totals intentionally omitted: extraction has not
            # run yet, so there is nothing measured — ``[]`` means "unknown"
            # and the char-based rules skip (r9 §C). No phantom zeros.
            signals = build_signals(
                query,
                search_result_counts=[len(probe)],
                errors=errors,
                markers=markers,
            )
            try:
                decision = decide(signals)
                mode, score, reasons = decision["mode"], decision["score"], decision["reasons"]
                reason = f"policy score {score:.2f}: " + ("; ".join(reasons) or "no rules fired")
            except Exception as exc:  # noqa: BLE001 — policy failure must not kill the query
                warnings.append(f"depth policy unavailable ({exc}) — defaulting to fast")
                mode, reason = "fast", "depth policy unavailable"
        yield {"type": "route", "depth": mode, "cached": False, "reason": reason}

        # 3) evidence building: fast = top <=4 extracts; deep = <=3 queries,
        #    <=8 extracts, trust-ordered. Deadline between stages: a spent
        #    budget skips the deep sub-searches and paid extraction — the
        #    probe snippets are the best available partial evidence.
        t = _ms()
        search_items: list[SearchItem] = list(probe)
        if mode == "deep" and not deadline_passed():
            # Probe already ran query #1; multi_part adds simple sub-splits,
            # capped so the total stays <= config.deep_search_queries.
            sub_queries = (
                split_query(query, max_parts=self.config.deep_search_queries - 1) if markers.get("multi_part") else []
            )
            for q in sub_queries:
                if deadline_passed():
                    break
                search_items.extend(self._safe_search(q, self.config.fast_max_results, errors, warnings))
        if deadline_passed():
            cap = self.config.deep_extract if mode == "deep" else self.config.fast_extract
            evidence = _search_items_to_evidence(search_items[: max(0, cap)])
            trust_by_url = {}
        elif mode == "deep":
            urls = _dedupe_urls(search_items)[: max(0, self.config.deep_extract)]
            evidence = self._extract_evidence(urls, search_items, errors, warnings)
            evidence, trust_by_url = self._trust_order(evidence, warnings)
            if not evidence:
                evidence = _search_items_to_evidence(search_items[: self.config.deep_extract])
        else:
            urls = _dedupe_urls(search_items)[: max(0, self.config.fast_extract)]
            evidence = self._extract_evidence(urls, search_items, errors, warnings)
            # R10-B: the fast path applies the SAME advisory trust ordering as
            # the deep path — ordering only; never drops/blocks sources. On
            # trust failure _trust_order keeps the search order and appends a
            # warning (existing semantics).
            evidence, trust_by_url = self._trust_order(evidence, warnings)
            if not evidence:
                evidence = _search_items_to_evidence(search_items[: self.config.fast_extract])
                evidence, trust_by_url = self._trust_order(evidence, warnings)
        timings["extract_ms"] = int(_ms() - t)

        # 4) synthesis (streamed deltas; fallback text on failure).
        t = _ms()
        synth = self._get_synth()
        answer_parts: list[str] = []
        if synth is None:
            answer = self._no_synth_answer(evidence)
            warnings.append("synthesizer unavailable — serving source list only")
            if answer:
                yield {"type": "delta", "text": answer}
        else:
            try:
                for piece in synth.stream(query, evidence, deep=(mode == "deep")):
                    if piece:
                        answer_parts.append(piece)
                        yield {"type": "delta", "text": piece}
            except Exception as exc:  # noqa: BLE001 — synth contract says no raise, belt+braces
                warnings.append(f"synthesis stream raised: {exc}")
            answer = "".join(answer_parts)
            warning = getattr(synth, "last_warning", None)
            if warning:
                warnings.append(str(warning))
            if not answer.strip():
                warnings.append("synthesis produced no text — serving source list only")
                answer = self._no_synth_answer(evidence)
                yield {"type": "delta", "text": answer}
        timings["synth_ms"] = int(_ms() - t)

        sources = [
            SourceRef(
                id=ev.id,
                title=ev.title,
                url=ev.url,
                quote=(ev.content or "")[:200].strip(),
                trust_score=(trust_by_url.get(_normalize_url(ev.url)) or {}).get("score"),
            )
            for ev in evidence
        ]

        # 5) deep only: fact_check gate -> research_pack -> AnswerCache.put.
        #    Publishing is optional work — skipped once the deadline is spent.
        if mode == "deep" and not deadline_passed():
            t = _ms()
            self._verify_and_publish(query, answer, evidence, trust_by_url, warnings)
            timings["verify_ms"] = int(_ms() - t)

        deadline_passed()  # catch overruns inside extract/synth on any mode
        timings["deadline_exceeded"] = int(deadline_hit)
        timings["total_ms"] = int(_ms() - t0)
        yield {
            "type": "done",
            "result": EngineResult(
                answer_markdown=answer,
                sources=sources,
                depth=mode,
                cached=False,
                reason=reason,
                warnings=warnings,
                timings_ms=timings,
            ),
        }

    # ---------- internals ----------

    def _note_timeout(self) -> None:
        """Count a deadline hit on the optional metrics sink; never raises."""
        metrics = getattr(self, "metrics", None)
        record = getattr(metrics, "record_timeout", None)
        if not callable(record):
            return
        try:
            record()
        except Exception:  # noqa: BLE001 — metrics must never kill the flow
            pass

    def _get_backend(self):
        if self._backend is None:
            from gateway.backends import create_backend

            self._backend = create_backend(self.config)
        return self._backend

    def _get_synth(self):
        if self._synth is None:
            try:
                from gateway.core.synthesis import Synthesizer

                self._synth = Synthesizer(
                    base_url=self.config.synth_base_url,
                    model=self.config.synth_model,
                    api_key=self.config.resolve_synth_api_key(),
                    timeout=self.config.synth_timeout,
                    extra_headers=getattr(self.config, "synth_headers", None) or None,
                )
            except Exception:
                return None
        return self._synth

    def _get_cache(self):
        if self._cache is None and not self._cache_failed:
            try:
                from gateway.core.cache import GatewayCache

                self._cache = GatewayCache(self.config.cache_db_path)
            except Exception as exc:  # noqa: BLE001 — cache loss degrades, never crashes
                self._cache_failed = True
                self._cache_error = f"{type(exc).__name__}: {exc}"
        return self._cache

    def _cache_get(self, query: str, warnings: list[str]) -> dict | None:
        cache = self._get_cache()
        if cache is None:
            return None
        try:
            return cache.get(query)
        except Exception as exc:  # noqa: BLE001 — a cache error is a miss, never a crash
            warnings.append(f"cache lookup failed ({exc}) — treating as miss")
            return None

    def _cache_put(self, pack: dict, warnings: list[str]) -> bool:
        cache = self._get_cache()
        if cache is None:
            warnings.append("no cache available — verified pack not published")
            return False
        try:
            cache.put(pack)
            return True
        except Exception as exc:  # noqa: BLE001 — publish failure is a warning
            warnings.append(f"cache publish failed: {exc}")
            return False

    def _safe_search(self, query: str, max_results: int, errors: list[str], warnings: list[str]) -> list[SearchItem]:
        try:
            return self._get_backend().search(query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 — backend failure must not kill the flow
            msg = f"{type(exc).__name__}: {exc}"
            errors.append(msg)
            warnings.append(f"search failed for {query!r}: {msg}")
            return []

    def _extract_evidence(
        self,
        urls: list[str],
        search_items: list[SearchItem],
        errors: list[str],
        warnings: list[str],
    ) -> list[EvidenceItem]:
        """Extract urls -> numbered evidence, titles back-filled from search."""
        if not urls:
            return []
        try:
            extracted = self._get_backend().extract(urls, char_limit=15000)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            errors.append(msg)
            warnings.append(f"extract failed: {msg}")
            return []
        titles = {_normalize_url(it.url): it.title for it in search_items}
        evidence: list[EvidenceItem] = []
        for item in extracted:
            if not isinstance(item, ExtractItem):
                continue
            if item.error and not item.content.strip():
                errors.append(f"extract error for {item.url}: {item.error}")
                continue
            title = item.title or titles.get(_normalize_url(item.url), "")
            evidence.append(EvidenceItem(id=len(evidence) + 1, title=title, url=item.url, content=item.content))
        return evidence

    def _trust_order(self, evidence: list[EvidenceItem], warnings: list[str]) -> tuple[list[EvidenceItem], dict]:
        """Trust-score ordering via repo ``trust.score_sources``; never blocks."""
        if not evidence:
            return evidence, {}
        try:
            import trust

            report = trust.score_sources(
                [
                    {
                        "url": ev.url,
                        "title": ev.title,
                        "snippet_only": not (ev.content or "").strip(),
                        "backend_error": False,
                    }
                    for ev in evidence
                ]
            )
        except Exception as exc:  # noqa: BLE001 — ordering is advisory
            warnings.append(f"trust scoring unavailable ({exc}) — keeping search order")
            return evidence, {}
        by_url = {_normalize_url(e.get("url", "")): e for e in report.get("sources", []) if isinstance(e, dict)}
        ordered = sorted(evidence, key=lambda ev: -(by_url.get(_normalize_url(ev.url)) or {}).get("score", 0.0))
        return [
            EvidenceItem(id=i + 1, title=ev.title, url=ev.url, content=ev.content) for i, ev in enumerate(ordered)
        ], by_url

    def _verify_and_publish(
        self,
        query: str,
        answer: str,
        evidence: list[EvidenceItem],
        trust_by_url: dict,
        warnings: list[str],
    ) -> bool:
        """fact_check gate -> research_pack.build_pack -> cache.put (deep only)."""
        try:
            import fact_check
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"fact_check unavailable ({exc}) — answer not published")
            return False
        tmp = Path(self.config.tmp_dir)
        try:
            tmp.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"cannot create {tmp} ({exc}) — answer not published")
            return False
        stamp = uuid.uuid4().hex[:12]
        draft_path = tmp / f"gateway-{stamp}-draft.md"
        ledger_path = tmp / f"gateway-{stamp}-ledger.json"
        ledger_sources = [
            {
                "id": ev.id,
                "url": ev.url,
                "title": ev.title,
                "accessed": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "quotes": [{"text": (ev.content or "")[:240].strip()}] if (ev.content or "").strip() else [],
            }
            for ev in evidence
        ]
        try:
            draft_path.write_text(answer, encoding="utf-8")
            ledger_path.write_text(
                json.dumps({"version": 1, "sources": ledger_sources}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"cannot write verification inputs ({exc}) — answer not published")
            return False
        try:
            report, exit_code, notices = fact_check.run_check(draft_path, ledger_path, judge="off")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"fact_check failed to run ({exc}) — answer not published")
            return False
        warnings.extend(str(n) for n in notices)
        if exit_code != 0:
            summary = report.get("summary") if isinstance(report, dict) else {}
            warnings.append(f"fact_check did not pass (flags={summary.get('flags_n', '?')}) — answer not published")
            return False
        try:
            import research_pack
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"research_pack unavailable ({exc}) — answer not published")
            return False
        try:
            pack, pack_warnings = research_pack.build_pack(
                query=query,
                ledger_sources=ledger_sources,
                mode="deep",
                trust_by_url=trust_by_url,
                answer_markdown=answer,
                fact_check_report=report,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"research_pack build failed ({exc}) — answer not published")
            return False
        warnings.extend(str(w) for w in pack_warnings)
        if self._cache_put(pack, warnings):
            return True
        return False

    def _result_from_pack(self, hit: dict, warnings: list[str], timings: dict, t0: float) -> EngineResult:
        pack = hit.get("pack") or {}
        answer = pack.get("answer_markdown") or ""
        if not answer.strip():
            warnings.append("cached pack has no answer_markdown")
        sources = [
            SourceRef(
                id=i + 1,
                title=str(s.get("title") or ""),
                url=str(s.get("url") or ""),
                quote=str(s.get("quote") or ""),
                trust_score=s.get("trust_score"),
            )
            for i, s in enumerate(pack.get("sources") or [])
            if isinstance(s, dict) and s.get("url")
        ]
        ttl = pack.get("ttl_days", "?")
        timings["total_ms"] = int(_ms() - t0)
        return EngineResult(
            answer_markdown=answer,
            sources=sources,
            depth="cache",
            cached=True,
            reason=f"fresh cache hit (age {hit.get('age_days', 0):.1f}d <= ttl {ttl}d)",
            warnings=warnings,
            timings_ms=timings,
        )

    def _no_synth_answer(self, evidence: list[EvidenceItem]) -> str:
        lines = ["*No synthesized answer available. Sources:*", "", "## Sources"]
        if evidence:
            lines.extend(f"[{ev.id}] {ev.title} — {ev.url}" for ev in evidence)
        else:
            lines.append("(no sources collected)")
        return "\n".join(lines)

    # ---------- readiness introspection (additive; for /readyz) ----------

    def status(self) -> dict:
        """Best-effort readiness snapshot; never raises."""
        out: dict = {
            "backend": {"name": None, "ok": False, "detail": "unresolved"},
            "cache": {"ok": False},
            "synth": {},
        }
        try:
            backend = self._get_backend()
            out["backend"] = {"name": getattr(backend, "name", type(backend).__name__), **backend.ping()}
        except Exception as exc:  # noqa: BLE001
            out["backend"] = {"name": None, "ok": False, "detail": str(exc)}
        cache = self._get_cache()
        if cache is None:
            out["cache"] = {"ok": False, "detail": self._cache_error or "cache unavailable"}
        else:
            try:
                probe = getattr(cache, "probe", None)
                if not callable(probe):
                    raise TypeError("cache object has no probe()")
                probe()  # real DB touch — object presence alone proved nothing
                out["cache"] = {"ok": True}
            except Exception as exc:  # noqa: BLE001 — readiness reports, never raises
                out["cache"] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        synth = self._get_synth()
        if synth is None:
            out["synth"] = {"ok": False}
        else:
            out["synth"] = {"ok": bool(synth.available()) if hasattr(synth, "available") else True}
        out["ready"] = bool(out["backend"].get("ok"))
        return out


def _dedupe_urls(items: list[SearchItem]) -> list[str]:
    """Unique URLs in first-seen order (normalized)."""
    seen: set[str] = set()
    urls: list[str] = []
    for item in items:
        key = _normalize_url(item.url)
        if key and key not in seen:
            seen.add(key)
            urls.append(item.url)
    return urls


def default_engine(config: GatewayConfig) -> Engine:
    """Build an ``Engine`` with default backend/synth/cache wiring (frozen §5)."""
    return Engine(config)
