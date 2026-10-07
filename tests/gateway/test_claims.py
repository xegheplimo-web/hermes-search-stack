"""Tests for gateway.core.claims (R15-B1; analysis/r15-interfaces.md §6.3).

Hermetic mechanical verification math; the optional local judge uses a
scripted fake llm — no network.
"""

from __future__ import annotations

import json

from gateway.core.claims import Claim, ClaimReport, ClaimVerdict, confidence_from, extract_claims, verify_claims
from gateway.protocols import EvidenceItem


def _ev(i: int, content: str) -> EvidenceItem:
    return EvidenceItem(id=i, title=f"E{i}", url=f"https://e.test/{i}", content=content)


# ---------- extract_claims ----------


def test_extract_splits_sentences_and_ids():
    claims = extract_claims("Alpha floods yearly [1]. Beta costs 50000 dong [2][3].\nLast line [1].")
    assert [c.citation_ids for c in claims] == [[1], [2, 3], [1]]
    assert claims[0].text == "Alpha floods yearly [1]."


def test_extract_dedupes_ids_and_drops_empties():
    claims = extract_claims("Cited twice [1][1][2].\n\n\n")
    assert len(claims) == 1
    assert claims[0].citation_ids == [1, 2]


def test_extract_excludes_sources_section():
    text = "Real claim [1].\n\n## Sources\n[1] Fake — https://x\n[2] Other — https://y"
    claims = extract_claims(text)
    assert [c.text for c in claims] == ["Real claim [1]."]


def test_extract_empty_input():
    assert extract_claims("") == []
    assert extract_claims(None) == []


# ---------- verify_claims: mechanical ----------


def test_supported_when_cited_and_overlapping():
    evidence = [_ev(1, "The river delta floods yearly during the rainy season")]
    report = verify_claims(extract_claims("The river delta floods yearly [1]."), evidence)
    assert report.claims[0].status == "supported"
    assert report.unsupported == []
    assert report.coverage == 1.0


def test_unsupported_when_cited_id_missing():
    evidence = [_ev(1, "content here")]
    report = verify_claims(extract_claims("Claim about things [9]."), evidence)
    assert report.claims[0].status == "unsupported"
    assert report.unsupported == ["Claim about things [9]."]
    assert report.coverage == 0.0


def test_unsupported_when_overlap_below_threshold():
    # claim tokens >=4: {alpha, beta, gamma, delta, epsilon, zeta, theta, kappa}
    evidence = [_ev(1, "only alpha appears in this content")]
    claim = extract_claims("Alpha beta gamma delta epsilon zeta theta kappa [1].")[0]
    report = verify_claims([claim], evidence, overlap=0.5)
    assert report.claims[0].status == "unsupported"


def test_supported_at_overlap_boundary():
    evidence = [_ev(1, "alpha beta")]
    # 2 of 4 claim tokens present -> 0.5 >= 0.5 supported; >= fails under 0.6
    claim = extract_claims("Alpha beta gamma delta [1].")[0]
    assert verify_claims([claim], evidence, overlap=0.5).claims[0].status == "supported"
    assert verify_claims([claim], evidence, overlap=0.6).claims[0].status == "unsupported"


def test_uncited_factual_for_digits_without_citation():
    report = verify_claims(extract_claims("It happened in 2024. It is nice."), [])
    statuses = [v.status for v in report.claims]
    assert statuses == ["uncited_factual", "uncited"]


def test_coverage_math_mixed_verdicts():
    evidence = [_ev(1, "alpha beta gamma delta")]
    answer = "Alpha beta gamma delta [1]. Missing proof [9]. Count is 7. Just words."
    report = verify_claims(extract_claims(answer), evidence)
    statuses = [v.status for v in report.claims]
    assert statuses == ["supported", "unsupported", "uncited_factual", "uncited"]
    # coverage = supported / (supported + unsupported + uncited_factual) = 1/3
    assert report.coverage == 1 / 3


def test_coverage_one_when_no_claims():
    assert verify_claims([], [_ev(1, "x")]).coverage == 1.0


def test_possible_contradictions_digit_sets_differ():
    evidence = [
        _ev(1, "population is 12 million in 2020"),
        _ev(2, "population is 5 million in 2021"),
        _ev(3, "population is 12 million in 2020"),
    ]
    claims = extract_claims("Population differs across sources [1][2]. Same numbers [1][3].")
    report = verify_claims(claims, evidence)
    assert report.possible_contradictions == ["Population differs across sources [1][2]."]


def test_dict_evidence_accepted():
    report = verify_claims(
        extract_claims("Alpha beta gamma [1]."),
        [{"id": 1, "content": "alpha beta gamma"}],
    )
    assert report.claims[0].status == "supported"


# ---------- judge="local" ----------


class _JudgeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, prompt, *, max_tokens):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_judge_local_applies_verdicts():
    claims = extract_claims("First claim [1]. Second claim [1].")
    llm = _JudgeLLM(json.dumps({"verdicts": ["unsupported", "supported"]}))
    report = verify_claims(claims, [_ev(1, "x")], judge="local", llm=llm)
    assert [v.status for v in report.claims] == ["unsupported", "supported"]
    assert llm.calls == 1  # capped ONE call


def test_judge_local_fail_open_all_unknown():
    claims = extract_claims("First claim [1]. Second claim [9].")
    for bad in ("garbage", json.dumps({"verdicts": ["supported"]}), RuntimeError("down")):
        llm = _JudgeLLM(bad)
        report = verify_claims(claims, [_ev(1, "x")], judge="local", llm=llm)
        assert [v.status for v in report.claims] == ["unknown", "unknown"], bad
        assert llm.calls == 1


def test_judge_local_without_llm_stays_mechanical():
    claims = extract_claims("Alpha beta gamma delta [1].")
    report = verify_claims(claims, [_ev(1, "alpha beta gamma delta")], judge="local", llm=None)
    assert report.claims[0].status == "supported"


def test_judge_invalid_value_treated_as_off():
    claims = extract_claims("Alpha beta gamma delta [1].")
    report = verify_claims(claims, [_ev(1, "alpha beta gamma delta")], judge="nonsense", llm=_JudgeLLM("x"))
    assert report.claims[0].status == "supported"


# ---------- confidence_from ----------


def _report(statuses, coverage=None) -> ClaimReport:
    report = ClaimReport()
    report.claims = [ClaimVerdict(claim=Claim(text=s), status=s) for s in statuses]
    if coverage is not None:
        report.coverage = coverage
    return report


def test_confidence_high():
    assert confidence_from(_report(["supported", "supported"], coverage=0.95)) == "high"


def test_confidence_medium_on_few_issues():
    assert confidence_from(_report(["supported", "unsupported"], coverage=0.5)) == "medium"
    assert confidence_from(_report(["unsupported"] * 2, coverage=0.1)) == "medium"
    assert confidence_from(_report(["uncited_factual"], coverage=0.4)) == "medium"


def test_confidence_medium_on_coverage():
    assert confidence_from(_report(["unsupported"] * 5, coverage=0.7)) == "medium"


def test_confidence_low():
    assert confidence_from(_report(["unsupported"] * 5, coverage=0.4)) == "low"


def test_confidence_high_requires_zero_issues():
    # coverage high but one uncited_factual issue -> not high
    assert confidence_from(_report(["supported", "supported", "uncited_factual"], coverage=0.95)) == "medium"
