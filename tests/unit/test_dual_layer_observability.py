"""Comprehensive Unit Tests for JARVIS Dual-Layer Observability (Milestone 12).

Validates:
1. Control Plane vs. Data Plane span classification and OTLP serialization.
2. Context propagation, parent-child span hierarchy, and trace ID inheritance.
3. High-assurance secret and PII redaction across strings, nested dictionaries, and tokens.
4. Operational metrics accounting (token usage, latency percentiles, verification rates).
5. In-memory and file span exporters, error capture, and tracer lifecycle.
"""

from pathlib import Path

import pytest

from jarvis.core.telemetry.metrics import OperationalMetrics
from jarvis.core.telemetry.redaction import PrivacyScrubber
from jarvis.core.telemetry.schemas import (
    SpanKind,
    SpanStatus,
    TelemetryLayer,
    TelemetrySpan,
)
from jarvis.core.telemetry.tracer import (
    FileSpanExporter,
    InMemorySpanExporter,
    Tracer,
)


@pytest.fixture
def temp_telemetry_dir(tmp_path: Path) -> Path:
    """Provide isolated directory for telemetry test logs."""
    d = tmp_path / "telemetry_test_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ==============================================================================
# 1. Span Models and OTLP Serialization Tests
# ==============================================================================


def test_span_layer_classification_and_otlp_serialization() -> None:
    """Verify dual-layer span classification and OpenTelemetry formatting."""
    control_span = TelemetrySpan(
        name="task.lifecycle.init",
        layer=TelemetryLayer.CONTROL_PLANE,
        kind=SpanKind.INTERNAL,
        attributes={"task_id": "task-100", "state": "PROPOSED"},
    )
    control_span.add_event("state_transition", {"from": "INIT", "to": "PROPOSED"})
    control_span.finish(status=SpanStatus.OK)

    otlp_dict = control_span.to_otlp_dict()
    assert otlp_dict["name"] == "task.lifecycle.init"
    assert otlp_dict["layer"] == "CONTROL_PLANE"
    assert otlp_dict["kind"] == "INTERNAL"
    assert otlp_dict["status"]["code"] == "OK"
    assert otlp_dict["duration_ms"] is not None
    assert otlp_dict["duration_ms"] >= 0.0
    assert len(otlp_dict["events"]) == 1
    assert otlp_dict["events"][0]["name"] == "state_transition"

    data_span = TelemetrySpan(
        name="model.completion",
        layer=TelemetryLayer.DATA_PLANE,
        kind=SpanKind.CLIENT,
        attributes={"model": "gemini-2.5-flash", "provider": "google"},
    )
    data_span.finish(status=SpanStatus.OK)
    data_otlp = data_span.to_otlp_dict()
    assert data_otlp["layer"] == "DATA_PLANE"
    assert data_otlp["kind"] == "CLIENT"


# ==============================================================================
# 2. Context Propagation & Span Hierarchy Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_tracer_hierarchy_and_context_propagation() -> None:
    """Verify parent trace ID inheritance and parent_span_id linking."""
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporters=[exporter])

    async with tracer.span(
        "orchestrator.turn",
        layer=TelemetryLayer.CONTROL_PLANE,
        attributes={"session_id": "sess-1"},
    ) as root_span:
        assert root_span.parent_span_id is None
        root_trace_id = root_span.trace_id

        async with tracer.span(
            "router.classify",
            layer=TelemetryLayer.CONTROL_PLANE,
        ) as child_span_1:
            assert child_span_1.trace_id == root_trace_id
            assert child_span_1.parent_span_id == root_span.span_id

        async with tracer.span(
            "model.completion",
            layer=TelemetryLayer.DATA_PLANE,
        ) as child_span_2:
            assert child_span_2.trace_id == root_trace_id
            assert child_span_2.parent_span_id == root_span.span_id

    # Verify exported hierarchy
    spans = exporter.get_spans()
    assert len(spans) == 3

    # Children finished before root
    span_map = {s.name: s for s in spans}
    assert span_map["router.classify"].parent_span_id == span_map["orchestrator.turn"].span_id
    assert span_map["model.completion"].parent_span_id == span_map["orchestrator.turn"].span_id
    assert span_map["model.completion"].layer == TelemetryLayer.DATA_PLANE
    assert span_map["orchestrator.turn"].layer == TelemetryLayer.CONTROL_PLANE


@pytest.mark.asyncio
async def test_tracer_error_capture() -> None:
    """Verify that exceptions inside spans automatically record error status and stack."""
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporters=[exporter])

    with pytest.raises(ValueError, match="Deliberate failure"):
        async with tracer.span("broker.dispatch", layer=TelemetryLayer.CONTROL_PLANE):
            raise ValueError("Deliberate failure")

    spans = exporter.get_spans()
    assert len(spans) == 1
    failed_span = spans[0]
    assert failed_span.status == SpanStatus.ERROR
    assert "Deliberate failure" in (failed_span.status_message or "")
    assert failed_span.attributes.get("error.type") == "ValueError"
    assert "Deliberate failure" in failed_span.attributes.get("error.stack", "")


# ==============================================================================
# 3. Automated Secret & PII Redaction Tests
# ==============================================================================


def test_privacy_scrubber_credential_redaction() -> None:
    """Verify redaction of diverse credentials, API keys, tokens, and private keys."""
    scrubber = PrivacyScrubber()

    # 1. API Keys
    mock_gemini_key = "AIzaSy" + "MockKeyForScrubberUnitTesting0123"
    text_with_keys = (
        f"Using Gemini {mock_gemini_key} "
        "and Groq gsk_1234567890abcdef1234567890abcdef "
        "and OpenAI sk-1234567890abcdef1234567890abcdef "
        "and OpenRouter sk-or-1234567890abcdef1234567890abcdef."
    )
    scrubbed_keys = scrubber.scrub_text(text_with_keys)
    assert "AIzaSy" not in scrubbed_keys
    assert "gsk_" not in scrubbed_keys
    assert "sk-" not in scrubbed_keys
    assert "[REDACTED:GEMINI_KEY:" in scrubbed_keys
    assert "[REDACTED:GROQ_KEY:" in scrubbed_keys

    # 2. Bearer Tokens and JWTs
    bearer_text = "Authorization: Bearer supersecrettoken_1234567890abc_xyz"
    scrubbed_bearer = scrubber.scrub_text(bearer_text)
    assert "supersecrettoken" not in scrubbed_bearer
    assert "[REDACTED:BEARER:" in scrubbed_bearer

    jwt_text = "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.do_not_leak_signature"
    scrubbed_jwt = scrubber.scrub_text(jwt_text)
    assert "eyJhbGciOi" not in scrubbed_jwt
    assert "[REDACTED:JWT:" in scrubbed_jwt

    # 3. Private Key
    private_key_text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0Y1+secretkeycontenthere...\n"
        "-----END RSA PRIVATE KEY-----"
    )
    scrubbed_pk = scrubber.scrub_text(private_key_text)
    assert "secretkeycontenthere" not in scrubbed_pk
    assert "[REDACTED:PRIVATE_KEY:" in scrubbed_pk


def test_privacy_scrubber_pii_redaction() -> None:
    """Verify redaction of emails, SSNs, and credit cards."""
    scrubber = PrivacyScrubber()

    pii_sample = (
        "Contact user at alice.developer@example.org with SSN 123-45-6789 "
        "and card 4111-2222-3333-4444 for payment."
    )
    scrubbed = scrubber.scrub_text(pii_sample)
    assert "alice.developer@example.org" not in scrubbed
    assert "123-45-6789" not in scrubbed
    assert "4111-2222-3333-4444" not in scrubbed
    assert "[REDACTED:EMAIL:" in scrubbed
    assert "[REDACTED:SSN:" in scrubbed
    assert "[REDACTED:CREDIT_CARD:" in scrubbed


def test_privacy_scrubber_nested_data_structure() -> None:
    """Verify recursive redaction over dictionaries and lists."""
    scrubber = PrivacyScrubber()

    mock_nested_key = "AIzaSy" + "SecretApiKeyHere1234567890123"
    raw_payload = {
        "user": "developer",
        "api_key": mock_nested_key,
        "nested": {
            "password": "SuperSecretPassword!",
            "public_metric": 42,
            "tokens_list": [
                "Bearer tok_alpha_1234567890",
                {"vault_key": "vault_secret_999"},
            ],
        },
    }

    cleaned = scrubber.scrub_data(raw_payload)
    assert cleaned["user"] == "developer"
    assert "AIzaSy" not in cleaned["api_key"]
    assert "[REDACTED:CREDENTIAL:" in cleaned["api_key"]
    assert "SuperSecretPassword" not in cleaned["nested"]["password"]
    assert cleaned["nested"]["public_metric"] == 42
    assert "tok_alpha" not in cleaned["nested"]["tokens_list"][0]
    assert "[REDACTED:CREDENTIAL:" in cleaned["nested"]["tokens_list"][1]["vault_key"]


def test_privacy_scrubber_internal_thought_stripping() -> None:
    """Verify that private model thought traces are stripped from telemetry."""
    scrubber = PrivacyScrubber(scrub_internal_thought=True)

    text = "Here is my reasoning: <thought>I should inspect the private key first</thought> Done."
    cleaned = scrubber.scrub_text(text)
    assert "inspect the private key" not in cleaned
    assert "[REDACTED:INTERNAL_REASONING]" in cleaned


# ==============================================================================
# 4. Operational Metrics & Resource Accounting Tests
# ==============================================================================


def test_operational_metrics_token_accounting() -> None:
    """Verify token accumulation, cost tracking, and per-model metrics."""
    metrics = OperationalMetrics()

    # Record two calls to Gemini Flash
    metrics.record_model_call(
        provider="google",
        model="gemini-2.5-flash",
        prompt_tokens=100,
        completion_tokens=50,
        duration_ms=250.0,
        cached_tokens=20,
        estimated_cost_usd=0.0001,
    )
    metrics.record_model_call(
        provider="google",
        model="gemini-2.5-flash",
        prompt_tokens=200,
        completion_tokens=100,
        duration_ms=400.0,
        cached_tokens=0,
        estimated_cost_usd=0.0002,
    )

    # Record one call to Groq
    metrics.record_model_call(
        provider="groq",
        model="llama-3.3-70b",
        prompt_tokens=300,
        completion_tokens=80,
        duration_ms=150.0,
        estimated_cost_usd=0.0003,
    )

    summary = metrics.get_summary()
    accounting = summary["token_accounting"]

    assert accounting["total_prompt_tokens"] == 600
    assert accounting["total_completion_tokens"] == 230
    assert accounting["total_tokens"] == 830
    assert accounting["total_cost_usd"] == 0.0006
    assert summary["provider_requests"]["google"] == 2
    assert summary["provider_requests"]["groq"] == 1


def test_operational_metrics_latencies_and_percentiles() -> None:
    """Verify latency distributions and percentile calculation."""
    metrics = OperationalMetrics()

    for d in [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]:
        metrics.record_latency("test:endpoint", d)

    summary = metrics.get_summary()
    lat = summary["latencies_ms"]["test:endpoint"]

    assert lat["count"] == 10
    assert lat["p50"] == 50.0
    assert lat["p90"] == 90.0
    assert lat["p99"] == 100.0
    assert lat["mean"] == 55.0


def test_operational_metrics_verification_and_dlq() -> None:
    """Verify verification pass rates and DLQ counts."""
    metrics = OperationalMetrics()

    metrics.record_verification(verified=True, duration_ms=15.0)
    metrics.record_verification(verified=True, duration_ms=12.0)
    metrics.record_verification(verified=False, duration_ms=20.0)

    metrics.record_dlq_event("quarantine")
    metrics.record_dlq_event("replay")

    summary = metrics.get_summary()
    assert summary["verification"]["total_attempts"] == 3
    assert summary["verification"]["passes"] == 2
    assert summary["verification"]["failures"] == 1
    assert summary["verification"]["success_rate_percent"] == 66.7
    assert summary["dead_letter_queue"]["quarantined_total"] == 1
    assert summary["dead_letter_queue"]["replayed_total"] == 1


# ==============================================================================
# 5. File Span Exporter Tests
# ==============================================================================


def test_file_span_exporter(temp_telemetry_dir: Path) -> None:
    """Verify that FileSpanExporter appends valid JSONL spans to disk."""
    log_file = temp_telemetry_dir / "test_spans.jsonl"
    exporter = FileSpanExporter(file_path=log_file)
    tracer = Tracer(exporters=[exporter])

    mock_tracer_token = "AIzaSy" + "TestKey12345678901234567890123"
    with tracer.sync_span(
        "sync.operation",
        layer=TelemetryLayer.CONTROL_PLANE,
        attributes={"secret_token": mock_tracer_token},
    ):
        pass

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "sync.operation" in content
    assert "CONTROL_PLANE" in content
    # Secret must be scrubbed from file!
    assert "AIzaSy" not in content
    assert "[REDACTED:CREDENTIAL:" in content
