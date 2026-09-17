"""Claim-Evidence Entailment and Epistemic Calibration Engine.

Evaluates scientific and medical claims against retrieved evidence using:
CLAIM -> RELEVANT EVIDENCE -> ENTAILMENT / SUPPORT CHECK -> SUPPORTED / QUALIFIED / UNSUPPORTED.
Supplemental defense-in-depth heuristics flag uncalibrated superlatives when evidence is absent.
"""

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ClaimSupportStatus(StrEnum):
    """Categorical classification of claim entailment against evidence."""

    SUPPORTED = "SUPPORTED"
    QUALIFIED = "QUALIFIED"
    UNSUPPORTED = "UNSUPPORTED"


class ClaimSupportResult(BaseModel):
    """Verification outcome of a single claim checked against authoritative evidence."""

    claim: str = Field(description="The scientific or medical statement evaluated.")
    status: ClaimSupportStatus = Field(description="Entailment status against evidence.")
    reason: str = Field(description="Detailed rationale for the support decision.")
    evidence_excerpt: str | None = Field(
        default=None, description="Relevant evidence snippet if found."
    )
    quantitative_assertions: list[str] = Field(
        default_factory=list, description="Specific metrics, percentages, or numbers asserted."
    )
    heuristic_alerts: list[str] = Field(
        default_factory=list, description="Supplemental phrasing warnings."
    )


# Supplemental defense-in-depth heuristics (secondary warnings, not sole proof)
UNCALIBRATED_HEURISTIC_PATTERNS: list[tuple[str, str]] = [
    (r"\bsuperhuman\s+accuracy\b", "superhuman accuracy"),
    (r"\bsuperhuman\s+precision\b", "superhuman precision"),
    (
        r"\bwell\s+before\s+(they\s+are\s+)?visible\s+to\s+the\s+human\s+eye\b",
        "visible to the human eye before clinical manifestation",
    ),
    (r"\bhigher\s+survival\s+rates\b", "higher survival rates"),
    (r"\bvastly\s+compressing\s+timelines\b", "vastly compressing timelines"),
    (r"\beradicat(e|ing|ion)\s+of\s+disease\b", "eradication of disease"),
    (r"\bflawless\s+diagnosis\b", "flawless diagnosis"),
]

EPISTEMIC_QUALIFIERS: list[str] = [
    "in retrospective benchmarks",
    "in controlled studies",
    "preliminary evidence suggests",
    "potential to",
    "under benchmark conditions",
    "clinical trials are required",
    "prospective validation is ongoing",
    "demonstrates promise",
    "subject to regulatory evaluation",
    "in algorithmic evaluations",
    "source-supported",
    "source-qualified",
]


def extract_quantitative_assertions(text: str) -> list[str]:
    """Extract specific percentages, multipliers, or numerical assertions from text."""
    patterns = [
        r"\b\d+(?:\.\d+)?%",  # percentages e.g. 37%, 95.4%
        r"\b(?:reduces?|increases?|improves?)\s+[\w\s]{1,20}\s+by\s+\d+(?:\.\d+)?%?",
        r"\b\d+[- ]fold\b",  # 10-fold
        r"\bp\s*[<>=]\s*0?\.\d+\b",  # p-values
    ]
    matches: list[str] = []
    for pat in patterns:
        for m in re.findall(pat, text, re.IGNORECASE):
            matches.append(m.strip())
    return list(dict.fromkeys(matches))


def audit_claim_evidence_support(
    claim: str,
    evidence_texts: list[str] | None = None,
) -> ClaimSupportResult:
    """Evaluate whether a claim is supported, qualified, or unsupported by retrieved evidence."""
    clean_claim = claim.strip()
    evidence_corpus = " ".join(evidence_texts or []).strip()
    lower_claim = clean_claim.lower()
    lower_evidence = evidence_corpus.lower()

    # 1. Extract quantitative assertions
    quant_assertions = extract_quantitative_assertions(clean_claim)

    # 2. Check supplemental phrasing heuristics (defense-in-depth)
    heuristic_alerts: list[str] = []
    for pat, label in UNCALIBRATED_HEURISTIC_PATTERNS:
        if re.search(pat, lower_claim, re.IGNORECASE):
            heuristic_alerts.append(label)

    # 3. If no evidence is provided at all
    if not evidence_corpus:
        if quant_assertions:
            return ClaimSupportResult(
                claim=clean_claim,
                status=ClaimSupportStatus.UNSUPPORTED,
                reason=f"Claim asserts quantitative statements {quant_assertions} with no retrieved evidence provided.",
                quantitative_assertions=quant_assertions,
                heuristic_alerts=heuristic_alerts,
            )
        # Check if epistemic qualifier exists in the claim itself
        has_qualifier = any(q in lower_claim for q in EPISTEMIC_QUALIFIERS)
        if has_qualifier:
            return ClaimSupportResult(
                claim=clean_claim,
                status=ClaimSupportStatus.QUALIFIED,
                reason="Claim contains epistemic qualification frames but lacks external retrieved grounding.",
                quantitative_assertions=quant_assertions,
                heuristic_alerts=heuristic_alerts,
            )
        return ClaimSupportResult(
            claim=clean_claim,
            status=ClaimSupportStatus.UNSUPPORTED,
            reason="Uncalibrated assertion without external evidence grounding or qualifying frame.",
            quantitative_assertions=quant_assertions,
            heuristic_alerts=heuristic_alerts,
        )

    # 4. Check quantitative entailment against evidence
    if quant_assertions:
        missing_numbers: list[str] = []
        for q in quant_assertions:
            # Check numbers/percentages in evidence
            nums = re.findall(r"\d+(?:\.\d+)?", q)
            for n in nums:
                if n not in lower_evidence:
                    missing_numbers.append(n)
        if missing_numbers:
            return ClaimSupportResult(
                claim=clean_claim,
                status=ClaimSupportStatus.UNSUPPORTED,
                reason=f"Quantitative assertion '{missing_numbers}' in claim is not present in retrieved evidence corpus.",
                quantitative_assertions=quant_assertions,
                heuristic_alerts=heuristic_alerts,
            )

    # 5. Check semantic grounding / entailment
    # Extract substantive nouns/verbs from claim
    claim_words = set(re.findall(r"\b[a-z]{4,}\b", lower_claim))
    stop_words = {
        "this",
        "that",
        "with",
        "from",
        "were",
        "been",
        "have",
        "more",
        "most",
        "also",
        "into",
    }
    substantive = claim_words - stop_words
    overlap_count = sum(1 for w in substantive if w in lower_evidence)
    overlap_ratio = overlap_count / max(len(substantive), 1)

    if overlap_ratio < 0.30:
        return ClaimSupportResult(
            claim=clean_claim,
            status=ClaimSupportStatus.UNSUPPORTED,
            reason="Claim asserts domain findings that have minimal overlap with retrieved evidence.",
            quantitative_assertions=quant_assertions,
            heuristic_alerts=heuristic_alerts,
        )

    # If the heuristic phrasing itself is directly confirmed in the evidence
    if heuristic_alerts and all(alert in lower_evidence for alert in heuristic_alerts):
        return ClaimSupportResult(
            claim=clean_claim,
            status=ClaimSupportStatus.SUPPORTED,
            reason="Claim assertions and findings are directly stated and entailed by retrieved evidence.",
            quantitative_assertions=quant_assertions,
            heuristic_alerts=heuristic_alerts,
        )

    # 6. Check if evidence contains qualifications
    evidence_has_qualifiers = any(
        term in lower_evidence
        for term in (
            "retrospective",
            "in-silico",
            "preliminary",
            "pilot",
            "scoping review",
            "further trials",
            "limitations",
        )
    )
    claim_has_qualifiers = any(q in lower_claim for q in EPISTEMIC_QUALIFIERS)

    if evidence_has_qualifiers and not claim_has_qualifiers and heuristic_alerts:
        return ClaimSupportResult(
            claim=clean_claim,
            status=ClaimSupportStatus.QUALIFIED,
            reason="Evidence indicates preliminary/retrospective limitations, while claim phrasing uses uncalibrated wording.",
            quantitative_assertions=quant_assertions,
            heuristic_alerts=heuristic_alerts,
        )

    # Genuinely supported claim: do not reject solely because of wording if evidence supports it
    return ClaimSupportResult(
        claim=clean_claim,
        status=ClaimSupportStatus.SUPPORTED,
        reason="Claim assertions are grounded in and entailed by retrieved evidence.",
        quantitative_assertions=quant_assertions,
        heuristic_alerts=heuristic_alerts,
    )


def audit_claim_epistemic_calibration(
    text: str,
    evidence_texts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Audit document sentences for uncalibrated claims or unevidenced assertions."""
    findings: list[dict[str, Any]] = []
    sentences = re.split(r"[.!?]\s+", text)

    for sentence in sentences:
        clean = sentence.strip()
        if not clean or len(clean.split()) < 4:
            continue

        res = audit_claim_evidence_support(clean, evidence_texts=evidence_texts)
        if res.status == ClaimSupportStatus.UNSUPPORTED:
            findings.append(
                {
                    "sentence": clean,
                    "claim_phrase": res.heuristic_alerts[0]
                    if res.heuristic_alerts
                    else "unsupported claim",
                    "issue": res.reason,
                    "status": res.status,
                }
            )
        elif res.status == ClaimSupportStatus.QUALIFIED and res.heuristic_alerts:
            findings.append(
                {
                    "sentence": clean,
                    "claim_phrase": res.heuristic_alerts[0],
                    "issue": res.reason,
                    "status": res.status,
                }
            )

    return findings
