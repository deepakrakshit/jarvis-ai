"""JARVIS Policy Simulator.

Enables safe dry-run policy inspection and auditing before real action dispatch:
    PolicySimulator.simulate(identity, task, capability, args, target, data_labels, environment) -> Decision
"""

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from jarvis.core.capabilities.manifest import CapabilityManifest
from jarvis.core.ifc.taint import LabeledData
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import AutonomyLevel, PolicyDecision
from jarvis.core.policy.engine import PolicyEngine

logger = get_logger(__name__)


class PolicySimulator:
    """Simulator for testing and previewing policy evaluation without side effects."""

    def __init__(self, engine: PolicyEngine | None = None) -> None:
        self.engine = engine or PolicyEngine()

    def simulate(
        self,
        capability: CapabilityManifest,
        arguments: dict[str, Any],
        task_id: str | UUID | None = None,
        target_resource: str | None = None,
        data_labels: LabeledData[Any] | None = None,
        autonomy_level: AutonomyLevel | None = None,
        environment: str = "development",
        agent_id: str = "simulator_agent",
        user_id: str = "simulator_user",
        workspace_root: Path | str | None = None,
    ) -> PolicyDecision:
        """Simulate policy evaluation for an intended tool invocation without durable state alteration."""
        tid = task_id or uuid4()

        # Temporarily override workspace or environment if specified
        original_ws = self.engine.workspace_root
        original_env = self.engine.environment
        try:
            if workspace_root is not None:
                self.engine.workspace_root = Path(workspace_root)
            self.engine.environment = environment

            decision = self.engine.evaluate_invocation(
                task_id=tid,
                manifest=capability,
                arguments=arguments,
                autonomy_level=autonomy_level,
                target_resource=target_resource,
                input_data=data_labels,
                agent_id=agent_id,
                user_id=user_id,
            )

            logger.info(
                "policy_simulation_completed",
                capability_id=capability.capability_id,
                decision=decision.decision.value,
                risk=decision.risk_score,
                obligations=decision.obligations,
            )
            return decision

        finally:
            self.engine.workspace_root = original_ws
            self.engine.environment = original_env
