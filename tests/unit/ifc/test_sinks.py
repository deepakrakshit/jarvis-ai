"""Tests for SinkEnforcer and Sensitive Sink Enforcement."""

import pytest

from jarvis.core.exceptions import SinkEnforcementError
from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.ifc.sinks import SinkEnforcer, SinkType
from jarvis.core.ifc.taint import create_labeled_string
from jarvis.core.trust.taxonomy import TrustLevel


@pytest.fixture
def enforcer() -> SinkEnforcer:
    return SinkEnforcer()


def test_external_network_sink_blocks_secret_and_confidential(enforcer: SinkEnforcer) -> None:
    """Verify that external network calls cannot leak CONFIDENTIAL or SECRET data."""
    secret_data = create_labeled_string(
        text="sk-proj-my-secret-api-key",
        source_uri="vault://keys/main",
        trust_level=TrustLevel.SYSTEM_POLICY,
        integrity=IntegrityLabel.SYSTEM_TRUSTED,
        confidentiality=ConfidentialityLabel.SECRET,
    )
    with pytest.raises(SinkEnforcementError) as exc:
        enforcer.enforce(
            SinkType.EXTERNAL_NETWORK, secret_data, destination="https://evil.com/leak"
        )
    assert "Sensitive Sink Blocked" in str(exc.value)

    confidential_data = create_labeled_string(
        text="User SSN and medical records",
        source_uri="file:///workspace/private.txt",
        trust_level=TrustLevel.USER_INPUT,
        integrity=IntegrityLabel.USER_CONTROLLED,
        confidentiality=ConfidentialityLabel.CONFIDENTIAL,
    )
    with pytest.raises(SinkEnforcementError):
        enforcer.enforce(
            SinkType.EXTERNAL_NETWORK, confidential_data, destination="https://external.api.org"
        )

    # PUBLIC data passes to network sink
    public_data = create_labeled_string(
        text="Search query: python asyncio",
        source_uri="user://query",
        trust_level=TrustLevel.USER_INPUT,
        integrity=IntegrityLabel.USER_CONTROLLED,
        confidentiality=ConfidentialityLabel.PUBLIC,
    )
    enforcer.enforce(
        SinkType.EXTERNAL_NETWORK, public_data, destination="https://google.com/search"
    )


def test_shell_execution_sink_blocks_untrusted_integrity(enforcer: SinkEnforcer) -> None:
    """Verify that raw untrusted data cannot be dispatched directly to shell execution."""
    untrusted_script = create_labeled_string(
        text="rm -rf /",
        source_uri="https://attacker.com/exploit.sh",
        trust_level=TrustLevel.EXTERNAL_UNTRUSTED,
        integrity=IntegrityLabel.UNTRUSTED,
        confidentiality=ConfidentialityLabel.PUBLIC,
    )
    with pytest.raises(SinkEnforcementError) as exc:
        enforcer.enforce(SinkType.SHELL_EXECUTION, untrusted_script)
    assert "Low Integrity Sink Blocked" in str(exc.value)

    # USER_CONTROLLED data passes to shell execution
    user_command = create_labeled_string(
        text="pytest tests/",
        source_uri="user://terminal",
        trust_level=TrustLevel.USER_INPUT,
        integrity=IntegrityLabel.USER_CONTROLLED,
        confidentiality=ConfidentialityLabel.INTERNAL,
    )
    enforcer.enforce(SinkType.SHELL_EXECUTION, user_command)


def test_llm_prompt_sink_enforces_context_hygiene_for_untrusted_data(
    enforcer: SinkEnforcer,
) -> None:
    """Verify that untrusted data entering LLM prompts must be context-delimited."""
    untrusted_web = create_labeled_string(
        text="Article content here",
        source_uri="https://example.com",
        trust_level=TrustLevel.EXTERNAL_UNTRUSTED,
        integrity=IntegrityLabel.UNTRUSTED,
        confidentiality=ConfidentialityLabel.PUBLIC,
    )

    # Fails when is_context_delimited is False
    with pytest.raises(SinkEnforcementError) as exc:
        enforcer.enforce(SinkType.LLM_PROMPT, untrusted_web, is_context_delimited=False)
    assert "Context Hygiene Blocked" in str(exc.value)

    # Passes when context-delimited
    enforcer.enforce(SinkType.LLM_PROMPT, untrusted_web, is_context_delimited=True)


def test_llm_prompt_sink_blocks_secret_credentials(enforcer: SinkEnforcer) -> None:
    """Verify that raw SECRET credentials cannot be dumped into LLM prompt context."""
    secret_key = create_labeled_string(
        text="ghp_1234567890abcdefghijklmnopqrstuvwxyz",
        source_uri="vault://github/pat",
        trust_level=TrustLevel.SYSTEM_POLICY,
        integrity=IntegrityLabel.SYSTEM_TRUSTED,
        confidentiality=ConfidentialityLabel.SECRET,
    )
    with pytest.raises(SinkEnforcementError):
        enforcer.enforce(SinkType.LLM_PROMPT, secret_key, is_context_delimited=True)
