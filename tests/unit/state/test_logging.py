"""Tests for JARVIS Structured Logging and Secret Redaction."""

from typing import Any

from jarvis.core.logging import (
    add_correlation_ids,
    bind_correlation,
    redact_secrets_processor,
)


def test_correlation_id_injection() -> None:
    """Verify correlation IDs are injected into log events when bound."""
    # Before binding, no correlation headers
    event_dict: dict[str, Any] = {"event": "test_event"}
    processed = add_correlation_ids(None, "info", dict(event_dict))
    assert "request_id" not in processed
    assert "task_id" not in processed

    # With binding
    with bind_correlation(request_id="req-99", task_id="task-42", agent_id="agent-01"):
        processed = add_correlation_ids(None, "info", dict(event_dict))
        assert processed["request_id"] == "req-99"
        assert processed["task_id"] == "task-42"
        assert processed["agent_id"] == "agent-01"

    # After exiting context manager, context is cleaned up
    processed_after = add_correlation_ids(None, "info", dict(event_dict))
    assert "request_id" not in processed_after


def test_secret_redaction_by_key_name() -> None:
    """Verify dictionary keys indicating secrets are scrubbed."""
    payload: dict[str, Any] = {
        "event": "login_attempt",
        "api_key": "raw_sensitive_key_123",
        "password": "super_secret_password",
        "nested": {
            "token": "bearer_abc_xyz",
            "safe_metric": 42,
        },
    }

    result = redact_secrets_processor(None, "info", payload)
    assert result["api_key"] == "[REDACTED]"
    assert result["password"] == "[REDACTED]"
    assert result["nested"]["token"] == "[REDACTED]"
    assert result["nested"]["safe_metric"] == 42


def test_secret_redaction_by_regex() -> None:
    """Verify raw credential strings inside text are scrubbed via regex."""
    google_key = "AIzaSy" + "A" * 33
    groq_key = "gsk_" + "B" * 50
    bearer_token = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"

    event: dict[str, Any] = {
        "message": f"Calling upstream with key {google_key} and groq {groq_key}",
        "auth_header": f"Header {bearer_token}",
    }

    result = redact_secrets_processor(None, "info", event)
    assert google_key not in result["message"]
    assert groq_key not in result["message"]
    assert bearer_token not in result["auth_header"]
    assert "[REDACTED]" in result["message"]
    assert "[REDACTED]" in result["auth_header"]
