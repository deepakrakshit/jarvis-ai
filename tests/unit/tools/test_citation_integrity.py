"""Unit tests for JARVIS Data-Driven Citation Integrity and Epistemic Calibration Subsystem.

Validates provider-based authoritative verification, multi-source reconciliation,
author discrepancy detection, anti-year-upgrading rules, and evidence-grounded claim support.
All publication fixtures are maintained strictly in tests/fixtures/, zero hardcoded in production.
"""

import json
from pathlib import Path
from typing import Any, cast

import pytest

from jarvis.core.verification.citation import (
    BaseCitationProvider,
    CitationAuthor,
    CitationMetadata,
    CitationRecord,
    CitationValidator,
    ClaimSupportStatus,
    ValidationState,
    audit_claim_evidence_support,
    validate_citation_metadata,
)
from jarvis.core.verification.reconciler import reconcile_provider_records


@pytest.fixture
def landmark_fixtures() -> dict[str, Any]:
    fixtures_path = (
        Path(__file__).resolve().parent.parent.parent
        / "fixtures"
        / "citation_cases"
        / "landmark_fixtures.json"
    )
    return cast("dict[str, Any]", json.loads(fixtures_path.read_text(encoding="utf-8")))


@pytest.fixture
def regression_fixtures() -> dict[str, Any]:
    fixtures_path = (
        Path(__file__).resolve().parent.parent.parent
        / "fixtures"
        / "citation_cases"
        / "regression_cases.json"
    )
    return cast("dict[str, Any]", json.loads(fixtures_path.read_text(encoding="utf-8")))


class MockAuthoritativeProvider(BaseCitationProvider):
    """Configurable mock provider for deterministic unit testing of edge cases."""

    def __init__(self, name: str, records_by_doi: dict[str, CitationRecord] | None = None) -> None:
        self._name = name
        self._records = records_by_doi or {}

    @property
    def provider_name(self) -> str:
        return self._name

    async def resolve_by_doi(self, doi: str) -> CitationRecord | None:
        return self._records.get(doi.lower())

    async def resolve_by_pmid(self, pmid: str) -> CitationRecord | None:
        for rec in self._records.values():
            if rec.pmid == pmid:
                return rec
        return None

    async def search(self, query: str, limit: int = 3) -> list[CitationRecord]:
        return list(self._records.values())[:limit]

    async def resolve_by_url(self, url: str) -> CitationRecord | None:
        for rec in self._records.values():
            if rec.canonical_url == url:
                return rec
        return None


class TestTenNegativeAndEdgeScenarios:
    """Rigorous verification of the 10 core negative and reconciliation scenarios required by the architecture."""

    @pytest.mark.asyncio
    async def test_1_correct_doi_and_title_wrong_author_invalid(self) -> None:
        """Scenario 1: Correct DOI + correct title + WRONG author -> INVALID."""
        auth_record = CitationRecord(
            source_id="mock:10.1038/s41591-025-03983-2",
            doi="10.1038/s41591-025-03983-2",
            title="Generative artificial intelligence in medicine",
            authors=[CitationAuthor(family="Teo", given="Zhen Ling", full_name="Zhen Ling Teo")],
            publication_year=2025,
            provider="mock_crossref",
        )
        validator = CitationValidator(
            providers=[
                MockAuthoritativeProvider("mock", {"10.1038/s41591-025-03983-2": auth_record})
            ]
        )

        citation = {
            "title": "Generative artificial intelligence in medicine",
            "authors": "Rajkomar, A. et al.",  # WRONG AUTHOR
            "year": 2025,
            "doi": "10.1038/s41591-025-03983-2",
        }
        res = await validator.verify_citation(citation, resolve_remote=True)

        assert res.state == ValidationState.INVALID
        assert any("not present in authoritative author metadata" in issue for issue in res.issues)

    @pytest.mark.asyncio
    async def test_2_correct_doi_wrong_year_invalid(self) -> None:
        """Scenario 2: Correct DOI + WRONG year (year upgrade) -> INVALID."""
        auth_record = CitationRecord(
            source_id="mock:10.7861/futurehosp.6-2-94",
            doi="10.7861/futurehosp.6-2-94",
            title="The potential for artificial intelligence in healthcare",
            authors=[
                CitationAuthor(family="Davenport", given="Thomas"),
                CitationAuthor(family="Kalakota", given="Ravi"),
            ],
            publication_year=2019,
            provider="mock_crossref",
        )
        validator = CitationValidator(
            providers=[
                MockAuthoritativeProvider("mock", {"10.7861/futurehosp.6-2-94": auth_record})
            ]
        )

        citation = {
            "title": "The potential for artificial intelligence in healthcare",
            "authors": "Davenport and Kalakota",
            "year": 2026,  # UPGRADED TO 2026
            "doi": "10.7861/futurehosp.6-2-94",
        }
        res = await validator.verify_citation(citation, resolve_remote=True)

        assert res.state == ValidationState.INVALID
        assert res.is_year_upgraded
        assert any("Publication year upgraded" in issue for issue in res.issues)

    @pytest.mark.asyncio
    async def test_3_correct_title_wrong_author_correct_url_invalid(self) -> None:
        """Scenario 3: Correct title + wrong author + correct URL -> INVALID."""
        auth_record = CitationRecord(
            source_id="mock:doi:10.1016/s2589-7500(24)00047-5",
            doi="10.1016/s2589-7500(24)00047-5",
            canonical_url="https://www.thelancet.com/journals/landig/article/PIIS2589-7500(24)00047-5/fulltext",
            title="Randomised controlled trials evaluating artificial intelligence in clinical practice: a scoping review",
            authors=[
                CitationAuthor(family="Han", given="Ryan"),
                CitationAuthor(family="Topol", given="Eric J"),
            ],
            publication_year=2024,
            provider="mock_publisher",
        )
        validator = CitationValidator(
            providers=[
                MockAuthoritativeProvider("mock", {"10.1016/s2589-7500(24)00047-5": auth_record})
            ]
        )

        # Model fabricated "Liu, X. et al."
        citation = {
            "title": "Randomised controlled trials evaluating artificial intelligence in clinical practice: a scoping review",
            "authors": "Liu, X. et al.",
            "year": 2024,
            "url": "https://www.thelancet.com/journals/landig/article/PIIS2589-7500(24)00047-5/fulltext",
            "doi": "10.1016/S2589-7500(24)00047-5",
        }
        res = await validator.verify_citation(citation, resolve_remote=True)

        assert res.state == ValidationState.INVALID
        assert any(
            "Claimed author 'Liu, X. et al.' is not present" in issue for issue in res.issues
        )

    def test_4_search_snippet_says_one_year_authoritative_provider_wins(self) -> None:
        """Scenario 4: Search snippet says one year, authoritative provider says another -> authoritative wins."""
        auth_record = CitationRecord(
            source_id="mock:10.1000/test",
            doi="10.1000/test",
            title="Comprehensive Medical Model Benchmark",
            authors=[CitationAuthor(family="Smith", given="John")],
            publication_year=2021,  # Real authoritative publication year
            provider="mock_crossref",
        )
        # Search snippet says 2025 (e.g. blog post reposted the snippet in 2025)
        search_sources = [
            {
                "title": "Comprehensive Medical Model Benchmark",
                "detected_year": 2025,
                "url": "https://example.org/blog",
            }
        ]
        citation = CitationMetadata(
            title="Comprehensive Medical Model Benchmark",
            authors="John Smith",
            year=2021,
            doi="10.1000/test",
        )
        # Validate against authoritative record
        res = validate_citation_metadata(
            citation, authoritative_record=auth_record, retrieved_sources=search_sources
        )

        assert res.state == ValidationState.VERIFIED
        assert res.detected_real_year == 2021
        assert len(res.issues) == 0

    @pytest.mark.asyncio
    async def test_5_unknown_arbitrary_paper_dynamically_verified(self) -> None:
        """Scenario 5: Unknown paper published tomorrow, not in any fixture -> system verifies dynamically."""
        new_paper = CitationRecord(
            source_id="mock:10.9999/future.2027.001",
            doi="10.9999/future.2027.001",
            title="Autonomous Robotic Microsurgery via Multimodal Transformers",
            authors=[
                CitationAuthor(family="Vanderbilt", given="Elena"),
                CitationAuthor(family="Chen", given="Wei"),
            ],
            publication_year=2027,
            venue="Journal of Robotic Medicine",
            provider="mock_crossref",
        )
        validator = CitationValidator(
            providers=[MockAuthoritativeProvider("mock", {"10.9999/future.2027.001": new_paper})]
        )

        citation = {
            "title": "Autonomous Robotic Microsurgery via Multimodal Transformers",
            "authors": "Elena Vanderbilt and Wei Chen",
            "year": 2027,
            "doi": "10.9999/future.2027.001",
        }
        res = await validator.verify_citation(citation, resolve_remote=True)

        assert res.state == ValidationState.VERIFIED
        assert res.reconciled_record is not None
        assert res.reconciled_record.publication_year == 2027
        assert res.reconciled_record.venue == "Journal of Robotic Medicine"

    @pytest.mark.asyncio
    async def test_6_missing_metadata_field_partially_verified(self) -> None:
        """Scenario 6: Missing metadata field -> PARTIALLY_VERIFIED / NEEDS_REVIEW, not silently fabricated."""
        incomplete_record = CitationRecord(
            source_id="mock:10.1000/incomplete",
            doi="10.1000/incomplete",
            title="Preliminary Notes on Neural Decoding",
            authors=[],  # Missing authors in provider
            publication_year=2023,
            provider="mock_crossref",
        )
        validator = CitationValidator(
            providers=[MockAuthoritativeProvider("mock", {"10.1000/incomplete": incomplete_record})]
        )

        citation = {
            "title": "Preliminary Notes on Neural Decoding",
            "year": 2023,
            "doi": "10.1000/incomplete",
        }
        res = await validator.verify_citation(citation, resolve_remote=True)

        assert res.state in (ValidationState.PARTIALLY_VERIFIED, ValidationState.NEEDS_REVIEW)

    def test_7_conflicting_providers_marked_conflict(self) -> None:
        """Scenario 7: Conflicting providers (e.g. Crossref says 2019, Publisher says 2024) -> CONFLICT."""
        rec1 = CitationRecord(
            source_id="p1",
            title="Study on Neural Plasticity",
            authors=[CitationAuthor(family="Jones", given="A")],
            publication_year=2019,
            provider="crossref",
        )
        rec2 = CitationRecord(
            source_id="p2",
            title="Study on Neural Plasticity",
            authors=[CitationAuthor(family="Jones", given="A")],
            publication_year=2024,  # Year conflict (5 years apart)
            provider="publisher_html",
        )
        _reconciled, state, issues = reconcile_provider_records([rec1, rec2])

        assert state == ValidationState.CONFLICT
        assert any("year conflict" in issue.lower() for issue in issues)

    @pytest.mark.asyncio
    async def test_8_llm_generated_author_not_in_authoritative_metadata_rejected(
        self, regression_fixtures: dict[str, Any]
    ) -> None:
        """Scenario 8: LLM-generated author not present in authoritative metadata -> reject (regression case)."""
        rajkomar_case = regression_fixtures["rajkomar_fabricated_author"]

        auth_record = CitationRecord(
            source_id=f"mock:{rajkomar_case['doi']}",
            doi=rajkomar_case["doi"],
            title="Generative artificial intelligence in medicine",
            authors=[
                CitationAuthor(family="Teo", given="Zhen Ling"),
                CitationAuthor(family="Thirunavukarasu", given="Arun James"),
                CitationAuthor(family="Langlotz", given="Curtis P"),
            ],
            publication_year=2025,
            provider="mock_crossref",
        )
        validator = CitationValidator(
            providers=[
                MockAuthoritativeProvider("mock", {rajkomar_case["doi"].lower(): auth_record})
            ]
        )

        res = await validator.verify_citation(rajkomar_case["raw_citation"], resolve_remote=True)

        assert res.state == ValidationState.INVALID
        assert not res.is_valid
        assert any("Rajkomar" in issue for issue in res.issues)

    def test_9_claim_with_no_suspicious_keyword_unsupported_by_evidence(self) -> None:
        """Scenario 9: Claim with no suspicious keyword but unsupported by evidence -> flag as unsupported."""
        claim = "AI reduces patient 30-day mortality by 37%."
        evidence = [
            "We evaluated a deep learning algorithm on retrospective radiograph benchmarks.",
            "The model achieved an area under the curve of 0.89 for nodule identification.",
            "Prospective trials are ongoing to evaluate clinical endpoints.",
        ]
        res = audit_claim_evidence_support(claim, evidence_texts=evidence)

        assert res.status == ClaimSupportStatus.UNSUPPORTED
        assert "37%" in res.quantitative_assertions
        assert any("37" in res.reason for _ in [1])

    def test_10_claim_with_suspicious_keyword_genuinely_supported_by_evidence_not_rejected(
        self,
    ) -> None:
        """Scenario 10: Claim with suspicious keyword but genuinely supported by evidence -> not automatically rejected."""
        claim = "In our controlled multi-center validation, the system demonstrated higher survival rates among early-detected cohorts."
        evidence = [
            "Controlled multi-center validation confirmed higher survival rates among early-detected cohorts (p < 0.01).",
            "Survival was tracked across 3-year follow-up intervals.",
        ]
        res = audit_claim_evidence_support(claim, evidence_texts=evidence)

        # Because evidence literally contains and entails the claim, it is supported
        assert res.status == ClaimSupportStatus.SUPPORTED
        assert "higher survival rates" in res.heuristic_alerts


class TestLiveScholarlyResolvers:
    """Test actual live dynamic resolution against Crossref and OpenAlex for public DOIs."""

    @pytest.mark.asyncio
    async def test_live_crossref_doi_lookup_davenport(
        self, landmark_fixtures: dict[str, Any]
    ) -> None:
        """Verify dynamic Crossref resolution for Davenport & Kalakota without hardcoded facts."""
        fixture = landmark_fixtures["davenport_kalakota"]
        doi = fixture["doi"]

        validator = CitationValidator()
        res = await validator.verify_citation(
            {
                "title": fixture["title"],
                "authors": fixture["authors"],
                "year": fixture["year"],
                "doi": doi,
            },
            resolve_remote=True,
        )

        assert res.state in (ValidationState.VERIFIED, ValidationState.PARTIALLY_VERIFIED)
        assert res.reconciled_record is not None
        assert res.reconciled_record.publication_year == 2019
        assert any("Davenport" in a.family for a in res.reconciled_record.authors)
        assert any("Kalakota" in a.family for a in res.reconciled_record.authors)
