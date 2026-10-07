"""gateway/core/claims.py — claim extraction + verification + confidence (R15-B1; §6.3).

Mechanical (non-LLM) claim checking for the xhigh deep path: an answer's
sentences become ``Claim`` records with ``[n]`` citation ids; each cited
claim is SUPPORTED iff every cited id exists in the evidence AND at least
``overlap`` of its content tokens appear in the cited contents' token
union. Sentences carrying digits/dates without a citation are flagged
``uncited_factual``. An optional ``judge="local"`` pass replaces verdicts
with ONE capped strict-JSON llm call — fail-open to all ``"unknown"``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_CITATION_RE = re.compile(r"\[(\d+)\]")
_TOKEN_RE = re.compile(r"[^\W_]+")  # letter/digit runs (unicode), no underscores
_DIGITS_RE = re.compile(r"\d+")
_SOURCES_HEADING_RE = re.compile(r"^##\s+sources\b", re.IGNORECASE)
_TOKEN_MIN_LEN = 4
_JUDGE_MAX_TOKENS = 600
_JUDGE_STATUSES = {"supported", "unsupported", "unknown"}


@dataclass(slots=True)
class Claim:
    """One answer sentence plus the ``[n]`` ids it cites."""

    text: str
    citation_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ClaimVerdict:
    """Per-claim outcome: supported | unsupported | uncited_factual | uncited | unknown."""

    claim: Claim
    status: str


@dataclass(slots=True)
class ClaimReport:
    """Frozen §6.3 report shape."""

    claims: list[ClaimVerdict] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    possible_contradictions: list[str] = field(default_factory=list)
    coverage: float = 1.0


def extract_claims(answer_md: str) -> list[Claim]:
    """Sentence-split *answer_md* into claims; ids parsed from ``[n]`` markers.

    Sentences are trimmed, empties dropped; the trailing ``## Sources``
    list (a citation index, not prose) is excluded.
    """
    lines: list[str] = []
    for line in str(answer_md or "").splitlines():
        if _SOURCES_HEADING_RE.match(line.strip()):
            break
        lines.append(line)
    out: list[Claim] = []
    for chunk in _SENT_SPLIT_RE.split("\n".join(lines)):
        sentence = chunk.strip()
        if not sentence:
            continue
        ids = list(dict.fromkeys(int(m) for m in _CITATION_RE.findall(sentence)))
        out.append(Claim(text=sentence, citation_ids=ids))
    return out


def _content_tokens(text: str) -> set[str]:
    """Lowercased content tokens, len >= 4, citation markers removed."""
    stripped = _CITATION_RE.sub(" ", str(text or ""))
    return {tok for tok in (t.lower() for t in _TOKEN_RE.findall(stripped)) if len(tok) >= _TOKEN_MIN_LEN}


def _evidence_by_id(evidence: list) -> dict[int, str]:
    """``{id: content}`` from EvidenceItem-like objects or dicts."""
    by_id: dict[int, str] = {}
    for ev in evidence or []:
        try:
            eid = int(ev["id"] if isinstance(ev, dict) else ev.id)
        except (TypeError, ValueError, KeyError):
            continue
        content = ev.get("content", "") if isinstance(ev, dict) else getattr(ev, "content", "")
        by_id[eid] = str(content or "")
    return by_id


def _mechanical_status(claim: Claim, by_id: dict[int, str], overlap: float) -> str:
    ids = claim.citation_ids
    if not ids:
        return "uncited_factual" if _DIGITS_RE.search(claim.text) else "uncited"
    if any(i not in by_id for i in ids):
        return "unsupported"
    tokens = _content_tokens(claim.text)
    if not tokens:
        return "supported"  # every cited id exists; nothing left to check
    union: set[str] = set()
    for i in ids:
        union |= _content_tokens(by_id[i])
    return "supported" if len(tokens & union) / len(tokens) >= overlap else "unsupported"


def _contradicted(claim: Claim, by_id: dict[int, str]) -> bool:
    """Cheap v1 check: >=2 cited evidences whose digit-token sets differ."""
    ids = claim.citation_ids
    if len(ids) < 2 or any(i not in by_id for i in ids):
        return False
    digit_sets = {frozenset(_DIGITS_RE.findall(by_id[i])) for i in ids}
    return len(digit_sets) > 1


def _judge_statuses(claims: list[Claim], by_id: dict[int, str], llm) -> list[str] | None:
    """ONE capped judge call -> per-claim statuses; ``None`` on any failure."""
    evidence_lines = "\n".join(f"[{i}] {content[:500]}" for i, content in sorted(by_id.items()))
    claim_lines = "\n".join(f"{n}: {c.text}" for n, c in enumerate(claims))
    prompt = (
        "Verify each numbered claim against the evidence passages. A claim is "
        '"supported" only if the evidence backs it, "unsupported" if it '
        'contradicts or lacks backing, "unknown" when unsure.\n'
        "Reply with ONLY a JSON object with one verdict per claim, in order:\n"
        '{"verdicts": ["supported", ...]}\n\n'
        f"Evidence:\n{evidence_lines or '(none)'}\n\nClaims:\n{claim_lines or '(none)'}"
    )
    try:
        raw = llm.complete(prompt, max_tokens=_JUDGE_MAX_TOKENS)
        data = json.loads(str(raw).strip())
        verdicts = data["verdicts"]
        if not isinstance(verdicts, list) or len(verdicts) != len(claims):
            return None
        return [v if v in _JUDGE_STATUSES else "unknown" for v in verdicts]
    except Exception:  # noqa: BLE001 — judge failure is always fail-open
        return None


def _tally(verdicts: list[ClaimVerdict], report: ClaimReport) -> None:
    sup = uns = unc_f = 0
    for verdict in verdicts:
        if verdict.status == "supported":
            sup += 1
        elif verdict.status == "unsupported":
            uns += 1
            report.unsupported.append(verdict.claim.text)
        elif verdict.status == "uncited_factual":
            unc_f += 1
    denom = sup + uns + unc_f
    report.coverage = sup / denom if denom else 1.0


def verify_claims(
    claims: list[Claim],
    evidence: list,
    *,
    judge: str = "off",
    overlap: float = 0.15,
    llm=None,
) -> ClaimReport:
    """Verify *claims* against *evidence*; mechanical by default (``judge="off"``).

    ``judge="local"`` with an llm replaces all verdicts with ONE capped
    strict-JSON call; any failure is fail-open — every verdict becomes
    ``"unknown"`` (never a fabricated negative). Without an llm the
    mechanical verdicts stand.
    """
    by_id = _evidence_by_id(evidence)
    claims = list(claims or [])
    report = ClaimReport()
    statuses: list[str] | None = None
    if judge == "local" and llm is not None and claims:
        statuses = _judge_statuses(claims, by_id, llm)  # ONE call, fail-open
        if statuses is None:
            statuses = ["unknown"] * len(claims)
    if statuses is None:
        statuses = [_mechanical_status(c, by_id, overlap) for c in claims]
    report.claims = [ClaimVerdict(claim=c, status=s) for c, s in zip(claims, statuses, strict=True)]
    _tally(report.claims, report)
    report.possible_contradictions = [c.text for c in claims if _contradicted(c, by_id)]
    return report


def confidence_from(report: ClaimReport) -> str:
    """``"high" | "medium" | "low"`` from a ClaimReport (§6.3)."""
    issues = sum(1 for v in report.claims if v.status in ("unsupported", "uncited_factual"))
    if issues == 0 and report.coverage >= 0.9:
        return "high"
    if issues <= 2 or report.coverage >= 0.6:
        return "medium"
    return "low"
