"""JARVIS Sensitive Sink Enforcement.

Guarantees that sensitive data (CONFIDENTIAL, SECRET) cannot leak into external
network sinks or untrusted tool parameters, and that UNTRUSTED data cannot execute
in high-integrity sinks (SHELL_EXECUTION) without validation.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from jarvis.core.exceptions import SinkEnforcementError
from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.ifc.taint import LabeledData
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class SinkType(StrEnum):
    """Categorization of execution, storage, and egress sinks."""

    EXTERNAL_NETWORK = "EXTERNAL_NETWORK"
    """Outbound network calls, HTTP requests, webhooks, remote APIs."""

    UNTRUSTED_TOOL_PARAMETER = "UNTRUSTED_TOOL_PARAMETER"
    """Parameters passed to MCP tools, external plugins, or third-party workers."""

    SHELL_EXECUTION = "SHELL_EXECUTION"
    """Commands dispatched to bash/powershell or sandbox processes."""

    FILE_SYSTEM_WRITE = "FILE_SYSTEM_WRITE"
    """Writing data or code to workspace or system files."""

    LLM_PROMPT = "LLM_PROMPT"
    """Context injected into LLM reasoning/completion prompts."""

    USER_DISPLAY = "USER_DISPLAY"
    """Content rendered to user UI, chat console, or Edge-TTS audio."""


class SinkPolicy(BaseModel):
    """Constraints governing data flowing into a specific SinkType."""

    sink_type: SinkType
    max_confidentiality: ConfidentialityLabel = ConfidentialityLabel.PUBLIC
    min_integrity: IntegrityLabel = IntegrityLabel.UNTRUSTED
    requires_context_delimiting_for_untrusted: bool = False
    whitelisted_destinations: list[str] = Field(default_factory=list)


# Canonical default policies governing sensitive sinks
DEFAULT_SINK_POLICIES: dict[SinkType, SinkPolicy] = {
    SinkType.EXTERNAL_NETWORK: SinkPolicy(
        sink_type=SinkType.EXTERNAL_NETWORK,
        max_confidentiality=ConfidentialityLabel.PUBLIC,
        min_integrity=IntegrityLabel.UNTRUSTED,
    ),
    SinkType.UNTRUSTED_TOOL_PARAMETER: SinkPolicy(
        sink_type=SinkType.UNTRUSTED_TOOL_PARAMETER,
        max_confidentiality=ConfidentialityLabel.INTERNAL,
        min_integrity=IntegrityLabel.UNTRUSTED,
    ),
    SinkType.SHELL_EXECUTION: SinkPolicy(
        sink_type=SinkType.SHELL_EXECUTION,
        max_confidentiality=ConfidentialityLabel.INTERNAL,
        min_integrity=IntegrityLabel.USER_CONTROLLED,  # Raw UNTRUSTED data cannot execute in shell
    ),
    SinkType.FILE_SYSTEM_WRITE: SinkPolicy(
        sink_type=SinkType.FILE_SYSTEM_WRITE,
        max_confidentiality=ConfidentialityLabel.CONFIDENTIAL,
        min_integrity=IntegrityLabel.UNTRUSTED,
    ),
    SinkType.LLM_PROMPT: SinkPolicy(
        sink_type=SinkType.LLM_PROMPT,
        max_confidentiality=ConfidentialityLabel.CONFIDENTIAL,  # SECRET credentials strictly blocked
        min_integrity=IntegrityLabel.UNTRUSTED,
        requires_context_delimiting_for_untrusted=True,
    ),
    SinkType.USER_DISPLAY: SinkPolicy(
        sink_type=SinkType.USER_DISPLAY,
        max_confidentiality=ConfidentialityLabel.CONFIDENTIAL,  # Raw secrets not spoken or displayed
        min_integrity=IntegrityLabel.UNTRUSTED,
    ),
}


class SinkEnforcer:
    """Evaluates data passing into sinks against formal IFC sink policies."""

    def __init__(self, policies: dict[SinkType, SinkPolicy] | None = None) -> None:
        self.policies = policies or DEFAULT_SINK_POLICIES

    def enforce(
        self,
        sink_type: SinkType,
        data: LabeledData[Any],
        destination: str | None = None,
        is_context_delimited: bool = False,
    ) -> None:
        """Validate that the given LabeledData complies with the sink policy.

        Raises SinkEnforcementError if the policy is violated.
        """
        policy = self.policies.get(sink_type)
        if not policy:
            raise SinkEnforcementError(f"No sink policy defined for sink type '{sink_type}'")

        # 1. Check Confidentiality ceiling
        if data.confidentiality.rank > policy.max_confidentiality.rank:
            # Check if destination is whitelisted for INTERNAL egress
            if (
                data.confidentiality == ConfidentialityLabel.INTERNAL
                and destination
                and any(dest in destination for dest in policy.whitelisted_destinations)
            ):
                pass
            else:
                logger.error(
                    "sink_enforcement_confidentiality_blocked",
                    sink=sink_type.value,
                    data_confidentiality=data.confidentiality.value,
                    max_allowed=policy.max_confidentiality.value,
                    source_uri=data.provenance.source_uri,
                    destination=destination,
                )
                raise SinkEnforcementError(
                    f"Sensitive Sink Blocked: Data with confidentiality '{data.confidentiality.value}' "
                    f"cannot be sent to sink '{sink_type.value}' (maximum allowed is "
                    f"'{policy.max_confidentiality.value}'). Source: {data.provenance.source_uri}"
                )

        # 2. Check Integrity floor
        if data.integrity.rank < policy.min_integrity.rank:
            logger.error(
                "sink_enforcement_integrity_blocked",
                sink=sink_type.value,
                data_integrity=data.integrity.value,
                min_required=policy.min_integrity.value,
                source_uri=data.provenance.source_uri,
            )
            raise SinkEnforcementError(
                f"Low Integrity Sink Blocked: Data with integrity '{data.integrity.value}' "
                f"cannot be executed in sink '{sink_type.value}' (minimum required is "
                f"'{policy.min_integrity.value}'). Untrusted data cannot directly "
                f"invoke shell execution."
            )

        # 3. Check Context Delimiting requirement for UNTRUSTED data
        if (
            policy.requires_context_delimiting_for_untrusted
            and data.integrity == IntegrityLabel.UNTRUSTED
            and not is_context_delimited
        ):
            logger.error(
                "sink_enforcement_undelimited_untrusted_blocked",
                sink=sink_type.value,
                source_uri=data.provenance.source_uri,
            )
            raise SinkEnforcementError(
                f"Context Hygiene Blocked: Untrusted data from '{data.provenance.source_uri}' "
                f"cannot be passed to '{sink_type.value}' without explicit context-delimiting boundaries."
            )

        logger.debug(
            "sink_enforcement_passed",
            sink=sink_type.value,
            data_confidentiality=data.confidentiality.value,
            data_integrity=data.integrity.value,
            destination=destination,
        )
