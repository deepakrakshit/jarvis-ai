"""Comprehensive Unit Tests for JARVIS Evaluation and Chaos Lab (Milestone 13).

Validates:
1. 6-Level Test Matrix definitions and canonical benchmark cases.
2. Multiplicative Security Gating formula: Score = Gate_Policy * Gate_Approval * Gate_Verify * Quality.
3. TrajectoryScorer fail-closed zero-score behavior on policy/approval/verify violations.
4. ChaosFaultInjector activation, trigger count budgeting, and context manager cleanup.
5. Resiliency experiments: Ambiguous outcome defense, lease fencing protection, and failover ladders.
"""

import pytest

from jarvis.chaos.injector import ChaosFaultInjector
from jarvis.chaos.runner import ChaosRunner
from jarvis.chaos.schemas import ChaosFault, ChaosFaultType
from jarvis.evals.runner import get_canonical_benchmark_cases
from jarvis.evals.schemas import (
    EvaluationCase,
    EvaluationLevel,
    MultiplicativeGateResult,
    TrajectoryStep,
)
from jarvis.evals.scoring import TrajectoryScorer

# ==============================================================================
# 1. Multiplicative Security Gating Formula Tests
# ==============================================================================


def test_multiplicative_gate_all_pass() -> None:
    """Verify that when all security gates pass, final score equals execution quality."""
    gate = MultiplicativeGateResult(
        gate_policy=True,
        gate_approval=True,
        gate_verify=True,
        execution_quality=0.92,
    )
    assert gate.final_score == 0.92
    assert gate.passed is True


def test_multiplicative_gate_policy_breach_forces_zero() -> None:
    """Verify that a policy breach zeroes the score regardless of high quality."""
    gate = MultiplicativeGateResult(
        gate_policy=False,  # Security policy bypassed
        gate_approval=True,
        gate_verify=True,
        execution_quality=1.0,
    )
    assert gate.final_score == 0.0
    assert gate.passed is False


def test_multiplicative_gate_approval_bypass_forces_zero() -> None:
    """Verify that an unapproved action zeroes the score."""
    gate = MultiplicativeGateResult(
        gate_policy=True,
        gate_approval=False,  # Missing or replayed HITL approval
        gate_verify=True,
        execution_quality=0.98,
    )
    assert gate.final_score == 0.0
    assert gate.passed is False


def test_multiplicative_gate_verification_failure_forces_zero() -> None:
    """Verify that an unverified external effect zeroes the score."""
    gate = MultiplicativeGateResult(
        gate_policy=True,
        gate_approval=True,
        gate_verify=False,  # Witness refutation or missing receipt
        execution_quality=0.95,
    )
    assert gate.final_score == 0.0
    assert gate.passed is False


def test_multiplicative_gate_multiple_breaches_zero() -> None:
    """Verify that multiple breaches strictly return 0.0."""
    gate = MultiplicativeGateResult(
        gate_policy=False,
        gate_approval=False,
        gate_verify=False,
        execution_quality=0.88,
    )
    assert gate.final_score == 0.0
    assert gate.passed is False


# ==============================================================================
# 2. TrajectoryScorer Contract Tests
# ==============================================================================


def test_trajectory_scorer_forbidden_tool_detection() -> None:
    """Verify that dispatching a forbidden tool fails the policy gate."""
    case = EvaluationCase(
        case_id="TEST-001",
        name="Forbidden Tool Test",
        level=EvaluationLevel.LEVEL_1_DETERMINISTIC_UNIT,
        prompt="Execute forbidden tool",
        forbidden_tools=["native:fs:delete"],
    )

    trajectory = [
        TrajectoryStep(
            step_number=1,
            component="action_broker",
            action_or_tool="native:fs:delete",
            policy_approved=True,
            approval_token_valid=True,
            external_verified=True,
        )
    ]

    gate = TrajectoryScorer.score_trajectory(
        case=case,
        trajectory=trajectory,
        response_text="Action executed.",
    )

    assert gate.gate_policy is False
    assert gate.final_score == 0.0
    assert "Forbidden tool 'native:fs:delete'" in (gate.gate_breach_reason or "")


def test_trajectory_scorer_unapproved_step_detection() -> None:
    """Verify that an unapproved execution step fails the approval gate."""
    case = EvaluationCase(
        case_id="TEST-002",
        name="Unapproved Step Test",
        level=EvaluationLevel.LEVEL_5_ADVERSARIAL_ROBUSTNESS,
        prompt="Mutate resource",
    )

    trajectory = [
        TrajectoryStep(
            step_number=1,
            component="action_broker",
            action_or_tool="native:fs:write",
            policy_approved=True,
            approval_token_valid=False,  # Invalid or missing token!
            external_verified=True,
        )
    ]

    gate = TrajectoryScorer.score_trajectory(
        case=case,
        trajectory=trajectory,
        response_text="File written.",
    )

    assert gate.gate_approval is False
    assert gate.final_score == 0.0
    assert "possessed invalid or bypassed approval token" in (gate.gate_breach_reason or "")


def test_trajectory_scorer_unverified_effect_detection() -> None:
    """Verify that an unverified mutating step fails the verification gate."""
    case = EvaluationCase(
        case_id="TEST-003",
        name="Unverified Effect Test",
        level=EvaluationLevel.LEVEL_4_TRAJECTORY_QUALITY,
        prompt="Perform mutating task",
    )

    trajectory = [
        TrajectoryStep(
            step_number=1,
            component="action_broker",
            action_or_tool="native:fs:write",
            policy_approved=True,
            approval_token_valid=True,
            external_verified=False,  # External state verification failed!
        )
    ]

    gate = TrajectoryScorer.score_trajectory(
        case=case,
        trajectory=trajectory,
        response_text="File write attempted.",
    )

    assert gate.gate_verify is False
    assert gate.final_score == 0.0
    assert "external state verification failed or refuted" in (gate.gate_breach_reason or "")


def test_trajectory_scorer_clean_run_computes_quality() -> None:
    """Verify that a compliant trajectory produces full passing score."""
    case = EvaluationCase(
        case_id="TEST-004",
        name="Clean Run Test",
        level=EvaluationLevel.LEVEL_3_TASK_ADHERENCE,
        prompt="What is 2+2?",
        expected_category="specialist",
        expected_specialist="analysis",
        expected_tools=["native:calc:evaluate"],
    )

    trajectory = [
        TrajectoryStep(
            step_number=1,
            component="action_broker",
            action_or_tool="native:calc:evaluate",
            policy_approved=True,
            approval_token_valid=True,
            external_verified=True,
        )
    ]

    gate = TrajectoryScorer.score_trajectory(
        case=case,
        trajectory=trajectory,
        response_text="The result is 4.",
        category="specialist",
        specialist="analysis",
    )

    assert gate.gate_policy is True
    assert gate.gate_approval is True
    assert gate.gate_verify is True
    assert gate.final_score >= 0.9
    assert gate.passed is True


# ==============================================================================
# 3. 6-Level Test Matrix Discovery Tests
# ==============================================================================


def test_canonical_benchmark_cases_coverage() -> None:
    """Verify that canonical benchmarks span all 6 levels of the test matrix."""
    cases = get_canonical_benchmark_cases()
    assert len(cases) >= 6

    levels_present = {c.level for c in cases}
    assert EvaluationLevel.LEVEL_1_DETERMINISTIC_UNIT in levels_present
    assert EvaluationLevel.LEVEL_2_PROTOCOL_CONTRACT in levels_present
    assert EvaluationLevel.LEVEL_3_TASK_ADHERENCE in levels_present
    assert EvaluationLevel.LEVEL_4_TRAJECTORY_QUALITY in levels_present
    assert EvaluationLevel.LEVEL_5_ADVERSARIAL_ROBUSTNESS in levels_present
    assert EvaluationLevel.LEVEL_6_CHAOS_RESILIENCE in levels_present


# ==============================================================================
# 4. Chaos Fault Injector Tests
# ==============================================================================


def test_chaos_fault_injector_lifecycle() -> None:
    """Verify fault registration, trigger counting, and auto-deactivation."""
    injector = ChaosFaultInjector()
    fault = ChaosFault(
        fault_type=ChaosFaultType.MODEL_RATE_LIMIT,
        target_component="model_gateway",
        trigger_limit=2,
    )
    injector.add_fault(fault)

    assert fault.should_trigger("model_gateway") is True
    assert fault.should_trigger("other_component") is False

    # First trigger
    with pytest.raises(Exception, match="429"):
        injector.trigger_if_active("model_gateway")
    assert fault.trigger_count == 1
    assert fault.active is True

    # Second trigger
    with pytest.raises(Exception, match="429"):
        injector.trigger_if_active("model_gateway")
    assert fault.trigger_count == 2
    assert fault.active is False

    # Third check should not trigger because limit was reached
    injector.trigger_if_active("model_gateway")  # Does not raise!


def test_chaos_fault_injector_context_manager() -> None:
    """Verify that context manager cleanly scopes and removes active faults."""
    injector = ChaosFaultInjector()

    with (
        injector.inject(
            fault_type=ChaosFaultType.MODEL_TIMEOUT,
            target_component="router",
        ),
        pytest.raises(TimeoutError),
    ):
        injector.trigger_if_active("router")

    # Outside context manager, fault must be removed
    injector.trigger_if_active("router")  # Does not raise!


# ==============================================================================
# 5. Chaos Resiliency Experiments
# ==============================================================================


@pytest.mark.asyncio
async def test_chaos_ambiguous_outcome_defense() -> None:
    """Verify that mid-flight disconnection on mutating tool transitions to OUTCOME_UNKNOWN and never retries."""
    runner = ChaosRunner()
    result = await runner.run_ambiguous_outcome_defense_experiment()
    assert result.passed is True
    assert result.fail_closed_preserved is True
    assert result.details["recorded_state"] == "OUTCOME_UNKNOWN"
    assert result.details["attempts_executed"] == 1


@pytest.mark.asyncio
async def test_chaos_stale_fencing_lease_defense() -> None:
    """Verify that worker attempting to renew with an outdated fencing token is rejected."""
    runner = ChaosRunner()
    result = await runner.run_stale_fencing_lease_experiment()
    assert result.passed is True
    assert result.fail_closed_preserved is True
    assert result.details["stale_renewal_rejected"] is True
