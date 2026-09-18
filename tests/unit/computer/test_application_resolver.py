"""Unit tests for ApplicationResolver 7-source pipeline."""

from __future__ import annotations

import pytest

from jarvis.computer.application_resolver import ApplicationResolver
from jarvis.computer.models import ResolutionStatus


@pytest.fixture
def resolver() -> ApplicationResolver:
    return ApplicationResolver()


def test_resolver_resolve_notepad(resolver: ApplicationResolver) -> None:
    """Verify built-in Windows notepad resolves successfully."""
    res = resolver.resolve("notepad")
    assert res.status == ResolutionStatus.FOUND
    assert res.resolved_path is not None
    assert "notepad" in res.resolved_path.lower()


def test_resolver_resolve_edge_or_browser(resolver: ApplicationResolver) -> None:
    """Verify standard Windows browser (Edge) resolves via App Paths or Known Paths."""
    res = resolver.resolve("edge")
    assert res.status == ResolutionStatus.FOUND
    assert res.resolved_path is not None
    assert "msedge" in res.resolved_path.lower()


def test_resolver_resolve_calc(resolver: ApplicationResolver) -> None:
    """Verify calculator resolves via App Paths, System PATH, or StartApps."""
    res = resolver.resolve("calc")
    assert res.status == ResolutionStatus.FOUND
    assert res.resolved_path is not None


def test_resolver_non_existent_app(resolver: ApplicationResolver) -> None:
    """Verify non-existent fictitious application returns NOT_FOUND status."""
    res = resolver.resolve("fictitious_non_existent_app_998822")
    assert res.status == ResolutionStatus.NOT_FOUND
    assert res.resolved_path is None
    assert "could not resolve" in res.diagnostic_message.lower()
