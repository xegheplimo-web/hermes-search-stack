"""OpenAI-compatible synthesis client for the gateway (r8 §5, D7).

``httpx`` is imported lazily inside ``__init__`` so unit tests (which inject a
fake synthesizer) never need the dependency. ``synthesize`` returns a full
answer string; ``stream`` yields text pieces. The prompt contract: answer in
the query's language (Vietnamese questions get Vietnamese answers), ground
ONLY on the supplied evidence, attach ``[n]`` citations per sentence, end with
a ``## Sources`` list, and state insufficiency instead of inventing. A
deterministic ``Current date:`` block (Asia/Ho_Chi_Minh) plus relative-date /
staleness rules keep time-relative answers honest (R10-A). HTTP or transport
failures never raise — they map to a minimal fallback text with
``last_warning`` set for the caller.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from gateway.protocols import EvidenceItem

_FALLBACK_CHUNK = 400

# Asia/Ho_Chi_Minh is fixed UTC+7 (no DST), so a constant offset keeps the
# injected clock correct even where the IANA tz database is absent (Windows).
_VN_TZ = timezone(timedelta(hours=7))

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

_SYSTEM = (
    "You are the synthesis engine of the Hermes search stack. Rules:\n"
    "- Answer in the SAME LANGUAGE as the user's question (a Vietnamese question gets a Vietnamese answer).\n"
    "- Ground every statement ONLY on the evidence passages below; never use outside knowledge.\n"
    "- Attach a citation marker [n] — matching the evidence item numbers — to every sentence.\n"
    "- If the evidence is insufficient, say so plainly instead of inventing facts.\n"
    "- Finish with a '## Sources' section listing '[n] title — url' for every cited item."
)

_SYSTEM_DATE_RULES = (
    "- Resolve every time-relative phrase in the question (e.g. 'hôm nay', 'ngày mai', 'tuần này', "
    "'mới nhất', 'today', 'tomorrow', 'this week', 'latest') against the 'Current date' line above, "
    "and state the resolved absolute date(s) explicitly in the answer.\n"
    "- For dynamic data (weather, prices, news, schedules, scores…), state the as-of date or period "
    "the evidence data refers to; when the freshest usable evidence predates the period the question "
    "asks about, say so plainly in days (e.g. 'cập nhật gần nhất 17/09, cách đây khoảng 3 tuần').\n"
    "- Never fabricate dates: if no evidence carries a usable date, state that limitation instead "
    "of guessing.\n"
    "- Do not attach dates to questions that are not time-sensitive."
)

_SYSTEM_DEEP_SUFFIX = (
    "\n- This is a DEEP research answer: structure it (short sections), cover every evidence item "
    "that matters, and prefer completeness over brevity."
)


def _evidence_block(evidence: list[EvidenceItem]) -> str:
    parts = []
    for ev in evidence:
        body = (ev.content or "").strip() or "(no extract content)"
        parts.append(f"[{ev.id}] {ev.title} — {ev.url}\n{body}")
    return "\n\n".join(parts) if parts else "(no evidence collected)"


def _now_line(now: datetime | None) -> str:
    """The 'Current date' block line; injectable ``now`` for tests, else real clock."""
    if now is None:
        moment = datetime.now(_VN_TZ)
    elif now.tzinfo is None:
        moment = now.replace(tzinfo=_VN_TZ)  # naive input = already VN wall time
    else:
        moment = now.astimezone(_VN_TZ)
    return f"Current date: {moment.date().isoformat()} ({_WEEKDAYS[moment.weekday()]}), Asia/Ho_Chi_Minh, UTC+7."


def _messages(query: str, evidence: list[EvidenceItem], deep: bool, now: datetime | None = None) -> list[dict]:
    system = (
        _SYSTEM
        + f"\n- {_now_line(now)}\n"
        + _SYSTEM_DATE_RULES
        + (_SYSTEM_DEEP_SUFFIX if deep else "\n- Keep the answer short: a few sentences.")
    )
    user = f"Question: {query}\n\nEvidence:\n{_evidence_block(evidence)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class Synthesizer:
    """Synchronous OpenAI-compatible chat-completions client."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        extra_headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.last_warning: str | None = None
        self._client = None
        self._init_error: str | None = None
        try:
            import httpx
        except ImportError as exc:
            self._init_error = f"httpx not installed ({exc}) — synthesis unavailable"
            return
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # OpenCode Zen/Go relay (the default synth target) rejects requests
        # without x-opencode-session (Hermes #105841); pin a stable per-client
        # affinity key unless the caller supplied one.
        if "opencode.ai" in self.base_url and "x-opencode-session" not in headers:
            headers["x-opencode-session"] = f"gateway-oneshot-{uuid.uuid4().hex[:16]}"
        try:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=timeout,
                headers=headers,
            )
        except Exception as exc:  # noqa: BLE001 — construction must not kill the engine
            self._init_error = f"synthesis client init failed: {exc}"

    def available(self) -> bool:
        """True when a live HTTP client exists (for readiness checks)."""
        return self._client is not None

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    # ---------- API ----------

    def synthesize(
        self,
        query: str,
        evidence: list[EvidenceItem],
        *,
        deep: bool = False,
        now: datetime | None = None,
    ) -> str:
        """Full non-streaming answer; falls back (never raises) on failure.

        ``now`` injects the 'Current date' block (tests); None = real clock.
        """
        self.last_warning = None
        if self._client is None:
            self.last_warning = self._init_error or "synthesis client unavailable"
            return self._fallback(query, evidence, self.last_warning)
        try:
            resp = self._client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": _messages(query, evidence, deep, now),
                    "temperature": 0.2,
                    "stream": False,
                },
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        except Exception as exc:  # noqa: BLE001 — fallback is the contract
            self.last_warning = f"synthesis request failed: {exc}"
            return self._fallback(query, evidence, self.last_warning)
        if not text:
            self.last_warning = "synthesis returned an empty completion"
            return self._fallback(query, evidence, self.last_warning)
        return text

    def stream(
        self,
        query: str,
        evidence: list[EvidenceItem],
        *,
        deep: bool = False,
        now: datetime | None = None,
    ) -> Iterator[str]:
        """Yield answer pieces (SSE deltas); on failure yields the fallback text.

        ``now`` injects the 'Current date' block (tests); None = real clock.
        """
        self.last_warning = None
        if self._client is None:
            self.last_warning = self._init_error or "synthesis client unavailable"
            yield from self._iter_fallback(query, evidence, self.last_warning)
            return
        try:
            with self._client.stream(
                "POST",
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": _messages(query, evidence, deep, now),
                    "temperature": 0.2,
                    "stream": True,
                },
            ) as resp:
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.read()[:200]!r}")
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: ") :].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
        except Exception as exc:  # noqa: BLE001 — fallback is the contract
            self.last_warning = f"synthesis stream failed: {exc}"
            yield from self._iter_fallback(query, evidence, self.last_warning)

    # ---------- fallback ----------

    def _fallback(self, query: str, evidence: list[EvidenceItem], reason: str) -> str:
        """Minimal answer that still surfaces the evidence honestly."""
        lines = [
            f"*Answer synthesis is unavailable ({reason}). The evidence collected for your question is listed below.*",
            "",
            "## Sources",
        ]
        if evidence:
            lines.extend(f"[{ev.id}] {ev.title} — {ev.url}" for ev in evidence)
        else:
            lines.append("(no sources were collected)")
        return "\n".join(lines)

    def _iter_fallback(self, query: str, evidence: list[EvidenceItem], reason: str) -> Iterator[str]:
        text = self._fallback(query, evidence, reason)
        for i in range(0, len(text), _FALLBACK_CHUNK):
            yield text[i : i + _FALLBACK_CHUNK]
